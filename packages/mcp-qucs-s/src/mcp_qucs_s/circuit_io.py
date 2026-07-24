"""Qucs/Qucs-S netlist and schematic import/export for ``CircuitDocument``."""

from __future__ import annotations

import hashlib
import re
import shlex
from pathlib import Path
from typing import Literal

from rf_mcp_common.circuit_ir import (
    CircuitAnalysis,
    CircuitComponent,
    CircuitDirective,
    CircuitDocument,
    CircuitGeometry,
    CircuitPort,
    CircuitProvenance,
    SourceLocation,
    UnsupportedConstruct,
    node_list,
)

QucsFormat = Literal["netlist", "schematic"]
Point = tuple[int, int]

_NETLIST_NODE_COUNTS = {
    "R": 2,
    "C": 2,
    "L": 2,
    "Pac": 2,
    "Vdc": 2,
    "Vac": 2,
    "Idc": 2,
    "Iac": 2,
    "Diode": 2,
    "TLIN": 2,
    "MLIN": 2,
    "CTLIN": 4,
    "Circulator": 3,
    "Sub": 0,
}
_KINDS = {
    "R": "resistor",
    "C": "capacitor",
    "L": "inductor",
    "Pac": "power_port",
    "Vdc": "voltage_source",
    "Vac": "voltage_source",
    "Idc": "current_source",
    "Iac": "current_source",
    "Diode": "diode",
    "TLIN": "transmission_line",
    "MLIN": "microstrip_line",
    "CTLIN": "coupled_transmission_line",
    "Circulator": "circulator",
    "Sub": "substrate",
}
_VALUE_PROPERTY = {"R": "R", "C": "C", "L": "L", "Vdc": "U", "Vac": "U", "Idc": "I", "Iac": "I"}
_SCHEMATIC_SUPPORTED = {"R", "C", "L", "Pac", "Vdc", "Vac", "Idc", "Iac"}
_SCHEMATIC_ANALYSES = {
    ".SP": "sparameters",
    ".AC": "ac",
    ".DC": "dc",
    ".TR": "transient",
    ".HB": "harmonic_balance",
}


def _tokens(text: str) -> list[str]:
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _properties(tokens: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for token in tokens:
        if "=" in token:
            name, value = token.split("=", 1)
            result[name] = value
    return result


def parse_qucs_netlist_text(text: str, *, artifact: str | None = None) -> CircuitDocument:
    """Parse Qucsator netlist connectivity and preserve dialect properties."""
    components: list[CircuitComponent] = []
    ports: list[CircuitPort] = []
    analyses: list[CircuitAnalysis] = []
    directives: list[CircuitDirective] = []
    unsupported: list[UnsupportedConstruct] = []
    nets: set[str] = set()

    for line_number, raw in enumerate(text.splitlines(), start=1):
        statement = raw.strip()
        if not statement or statement.startswith("#"):
            continue
        location = SourceLocation(artifact=artifact, line=line_number)
        if statement.startswith("."):
            directives.append(CircuitDirective(text=statement, dialect="qucs", source=location))
            token = statement.split(maxsplit=1)[0]
            analysis_type = token.split(":", 1)[0]
            if analysis_type in _SCHEMATIC_ANALYSES:
                analyses.append(
                    CircuitAnalysis(
                        id=token.removeprefix(".").replace(":", "_"),
                        kind=_SCHEMATIC_ANALYSES[analysis_type],  # type: ignore[arg-type]
                        parameters=dict(_properties(_tokens(statement)[1:])),
                    )
                )
            else:
                unsupported.append(
                    UnsupportedConstruct(
                        code="qucs.analysis",
                        message=f"analysis/directive {analysis_type!r} has no normalized semantics",
                        raw=statement,
                        location=location,
                        severity="warning",
                    )
                )
            continue
        try:
            tokens = _tokens(statement)
        except ValueError as exc:
            unsupported.append(
                UnsupportedConstruct(
                    code="qucs.tokenization",
                    message=str(exc),
                    raw=statement,
                    location=location,
                )
            )
            continue
        if not tokens or ":" not in tokens[0]:
            unsupported.append(
                UnsupportedConstruct(
                    code="qucs.statement",
                    message="expected model:instance token",
                    raw=statement,
                    location=location,
                )
            )
            continue
        model, refdes = tokens[0].split(":", 1)
        if model not in _NETLIST_NODE_COUNTS:
            unsupported.append(
                UnsupportedConstruct(
                    code="qucs.component",
                    message=f"component model {model!r} is not supported",
                    raw=statement,
                    location=location,
                )
            )
            continue
        node_count = _NETLIST_NODE_COUNTS[model]
        if len(tokens) < node_count + 1:
            unsupported.append(
                UnsupportedConstruct(
                    code="qucs.component_shape",
                    message=f"{model} requires {node_count} nodes",
                    raw=statement,
                    location=location,
                )
            )
            continue
        component_nets = [
            "0" if token.lower() == "gnd" else token for token in tokens[1 : 1 + node_count]
        ]
        properties: dict[str, str | float | int | bool] = dict(
            _properties(tokens[1 + node_count :])
        )
        nets.update(component_nets)
        if model == "Sub":
            directives.append(CircuitDirective(text=statement, dialect="qucs", source=location))
            continue
        if model == "Pac":
            try:
                number = int(properties.get("Num", refdes.removeprefix("P")))
            except ValueError:
                number = len(ports) + 1
            impedance_raw = properties.get("Z")
            impedance = _leading_float(str(impedance_raw) if impedance_raw is not None else None)
            ports.append(
                CircuitPort(
                    name=refdes,
                    number=number,
                    positive_net=component_nets[0],
                    negative_net=component_nets[1],
                    impedance_ohm=impedance if impedance and impedance > 0 else None,
                )
            )
        value_key = _VALUE_PROPERTY.get(model)
        components.append(
            CircuitComponent(
                refdes=refdes,
                kind=_KINDS[model],
                pins={str(index): net for index, net in enumerate(component_nets, start=1)},
                value=properties.pop(value_key, None) if value_key else None,
                parameters=properties,
                attributes={"qucs_model": model},
                source=location,
            )
        )

    source_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    document = CircuitDocument(
        document_id=f"qucsnet-{source_sha[:16]}",
        title=artifact,
        source_format="qucs_netlist",
        source_dialect="qucsator",
        nodes=node_list(nets),
        components=components,
        ports=ports,
        analyses=analyses,
        directives=directives,
        provenance=CircuitProvenance(
            source_artifact=artifact,
            source_sha256=source_sha,
            importer="mcp_qucs_s.circuit_io.parse_qucs_netlist_text",
        ),
        unsupported=unsupported,
        metadata={"source_text": text},
    )
    document.metadata["imported_fingerprint"] = document.electrical_fingerprint()
    return document


def _leading_float(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.match(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", value)
    return float(match.group(0)) if match else None


def _quote(value: str | float | int | bool) -> str:
    return f'"{value}"'


def export_qucs_netlist_text(document: CircuitDocument, *, preserve_source: bool = True) -> str:
    """Export a supported Qucs netlist document."""
    document.require_supported()
    if (
        preserve_source
        and document.source_format == "qucs_netlist"
        and isinstance(document.metadata.get("source_text"), str)
        and document.metadata.get("imported_fingerprint") == document.electrical_fingerprint()
    ):
        return str(document.metadata["source_text"])
    lines = [f"# CircuitDocument {document.document_id}"]
    for component in document.components:
        model = component.attributes.get("qucs_model")
        if not model:
            raise ValueError(f"{component.refdes}: missing qucs_model attribute")
        nets = [net for _, net in sorted(component.pins.items(), key=lambda item: int(item[0]))]
        nets = ["gnd" if net == "0" else net for net in nets]
        properties = dict(component.parameters)
        value_key = _VALUE_PROPERTY.get(model)
        if value_key and component.value is not None:
            properties = {value_key: str(component.value), **properties}
        rendered = " ".join(f"{name}={_quote(value)}" for name, value in properties.items())
        lines.append(" ".join([f"{model}:{component.refdes}", *nets, rendered]).rstrip())
    lines.extend(directive.text for directive in document.directives)
    return "\n".join(lines) + "\n"


class _Union:
    def __init__(self) -> None:
        self.parent: dict[Point, Point] = {}

    def find(self, point: Point) -> Point:
        self.parent.setdefault(point, point)
        root = point
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[point] != root:
            self.parent[point], point = root, self.parent[point]
        return root

    def union(self, left: Point, right: Point) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _on_segment(point: Point, segment: tuple[int, int, int, int]) -> bool:
    x, y = point
    x1, y1, x2, y2 = segment
    return (x1 == x2 and x == x1 and min(y1, y2) <= y <= max(y1, y2)) or (
        y1 == y2 and y == y1 and min(x1, x2) <= x <= max(x1, x2)
    )


def _schematic_pins(x: int, y: int, mirror: int, rotate: int) -> tuple[Point, Point]:
    first, second = ((x - 30, y), (x + 30, y)) if rotate % 2 == 0 else ((x, y - 30), (x, y + 30))
    return (second, first) if mirror else (first, second)


def parse_qucs_schematic_text(text: str, *, artifact: str | None = None) -> CircuitDocument:
    """Parse a connectivity-safe subset of the documented Qucs ``.sch`` format.

    The supported subset is R/L/C and two-terminal source/port components on
    orthogonal wires.  Every other component is retained as a blocking
    diagnostic so a caller cannot mistake a partial drawing for the circuit.
    """
    section: str | None = None
    component_rows: list[tuple[int, str, list[str]]] = []
    wires: list[tuple[int, int, int, int, str, int]] = []
    grounds: list[Point] = []
    unsupported: list[UnsupportedConstruct] = []
    analyses: list[CircuitAnalysis] = []
    directives: list[CircuitDirective] = []

    for line_number, raw in enumerate(text.splitlines(), start=1):
        statement = raw.strip()
        if statement in {
            "<Components>",
            "<Wires>",
            "<Properties>",
            "<Diagrams>",
            "<Paintings>",
            "<Symbol>",
        }:
            section = statement[1:-1]
            continue
        if statement.startswith("</"):
            section = None
            continue
        if not statement.startswith("<") or not statement.endswith(">"):
            continue
        body = statement[1:-1]
        if section == "Components":
            try:
                tokens = _tokens(body)
            except ValueError as exc:
                unsupported.append(
                    UnsupportedConstruct(
                        code="qucs.schematic_tokenization",
                        message=str(exc),
                        raw=statement,
                        location=SourceLocation(artifact=artifact, line=line_number),
                    )
                )
                continue
            if len(tokens) < 9:
                unsupported.append(
                    UnsupportedConstruct(
                        code="qucs.schematic_component_shape",
                        message="component record has fewer than nine fixed fields",
                        raw=statement,
                        location=SourceLocation(artifact=artifact, line=line_number),
                    )
                )
                continue
            model = tokens[0]
            if model == "GND":
                grounds.append((int(tokens[3]), int(tokens[4]) - 30))
            elif model.startswith("."):
                analysis_type = model.split(":", 1)[0]
                if analysis_type in _SCHEMATIC_ANALYSES:
                    analyses.append(
                        CircuitAnalysis(
                            id=tokens[1],
                            kind=_SCHEMATIC_ANALYSES[analysis_type],  # type: ignore[arg-type]
                            parameters={"schematic_record": statement},
                        )
                    )
                directives.append(
                    CircuitDirective(
                        text=statement,
                        dialect="qucs_schematic",
                        source=SourceLocation(artifact=artifact, line=line_number),
                    )
                )
            elif model not in _SCHEMATIC_SUPPORTED:
                unsupported.append(
                    UnsupportedConstruct(
                        code="qucs.schematic_component",
                        message=f"component model {model!r} has no registered schematic pin geometry",
                        raw=statement,
                        location=SourceLocation(artifact=artifact, line=line_number),
                    )
                )
            else:
                component_rows.append((line_number, statement, tokens))
        elif section == "Wires":
            tokens = _tokens(body)
            if len(tokens) < 4:
                unsupported.append(
                    UnsupportedConstruct(
                        code="qucs.schematic_wire_shape",
                        message="wire record has fewer than four coordinates",
                        raw=statement,
                        location=SourceLocation(artifact=artifact, line=line_number),
                    )
                )
                continue
            x1, y1, x2, y2 = (int(token) for token in tokens[:4])
            if x1 != x2 and y1 != y2:
                unsupported.append(
                    UnsupportedConstruct(
                        code="qucs.diagonal_wire",
                        message="diagonal wire connectivity is not supported",
                        raw=statement,
                        location=SourceLocation(artifact=artifact, line=line_number),
                    )
                )
            label = tokens[4] if len(tokens) > 4 else ""
            wires.append((x1, y1, x2, y2, label, line_number))

    union = _Union()
    points: set[Point] = set(grounds)
    row_pins: list[tuple[tuple[int, str, list[str]], tuple[Point, Point]]] = []
    for row in component_rows:
        tokens = row[2]
        pins = _schematic_pins(
            int(tokens[3]),
            int(tokens[4]),
            int(tokens[7]),
            int(tokens[8]),
        )
        points.update(pins)
        row_pins.append((row, pins))
    for x1, y1, x2, y2, _, _ in wires:
        points.update({(x1, y1), (x2, y2)})
    for x1, y1, x2, y2, _, _ in wires:
        segment = (x1, y1, x2, y2)
        union.union((x1, y1), (x2, y2))
        for point in points:
            if _on_segment(point, segment):
                union.union((x1, y1), point)
    for point in points:
        union.find(point)

    root_names: dict[Point, str] = {}
    for ground in grounds:
        root_names[union.find(ground)] = "0"
    for x1, y1, _, _, label, _ in wires:
        if label:
            root = union.find((x1, y1))
            if root_names.get(root) != "0":
                root_names[root] = label
    counter = 1
    for point in sorted(points):
        root = union.find(point)
        if root not in root_names:
            root_names[root] = f"_net{counter}"
            counter += 1

    components: list[CircuitComponent] = []
    ports: list[CircuitPort] = []
    nets: set[str] = set(root_names.values())
    for (line_number, statement, tokens), pins in row_pins:
        model, refdes = tokens[0], tokens[1]
        pin_map = {"1": root_names[union.find(pins[0])], "2": root_names[union.find(pins[1])]}
        value = tokens[9] if len(tokens) > 9 else None
        component = CircuitComponent(
            refdes=refdes,
            kind=_KINDS[model],
            pins=pin_map,
            value=value,
            attributes={"qucs_model": model, "schematic_record": statement},
            geometry=CircuitGeometry(
                x=int(tokens[3]),
                y=int(tokens[4]),
                rotation_deg=(int(tokens[8]) % 4) * 90,
                mirrored=bool(int(tokens[7])),
                source_token=model,
            ),
            source=SourceLocation(artifact=artifact, line=line_number),
        )
        components.append(component)
        if model == "Pac":
            number = int(re.sub(r"\D", "", refdes) or len(ports) + 1)
            ports.append(
                CircuitPort(
                    name=refdes,
                    number=number,
                    positive_net=pin_map["1"],
                    negative_net=pin_map["2"],
                    impedance_ohm=_leading_float(value),
                )
            )

    source_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    document = CircuitDocument(
        document_id=f"qucssch-{source_sha[:16]}",
        title=artifact,
        source_format="qucs_schematic",
        source_dialect="qucs",
        nodes=node_list(nets),
        components=components,
        ports=ports,
        analyses=analyses,
        directives=directives,
        provenance=CircuitProvenance(
            source_artifact=artifact,
            source_sha256=source_sha,
            importer="mcp_qucs_s.circuit_io.parse_qucs_schematic_text",
        ),
        unsupported=unsupported,
        metadata={"source_text": text},
    )
    document.metadata["imported_fingerprint"] = document.electrical_fingerprint()
    return document


def export_qucs_schematic_text(document: CircuitDocument) -> str:
    """Export an imported schematic, rewriting supported component values."""
    document.require_supported()
    if document.source_format != "qucs_schematic":
        raise ValueError("Qucs schematic export requires an imported Qucs schematic")
    source_text = document.metadata.get("source_text")
    if not isinstance(source_text, str):
        raise ValueError("Qucs schematic document has no preserved source")
    if document.metadata.get("imported_fingerprint") == document.electrical_fingerprint():
        return source_text
    values = {component.refdes: component.value for component in document.components}
    output: list[str] = []
    section: str | None = None
    for raw in source_text.splitlines():
        statement = raw.strip()
        if statement == "<Components>":
            section = "Components"
        elif statement == "</Components>":
            section = None
        elif section == "Components" and statement.startswith("<") and statement.endswith(">"):
            body = statement[1:-1]
            tokens = _tokens(body)
            if len(tokens) >= 10 and tokens[1] in values and values[tokens[1]] is not None:
                # The first property (field 9) is the primary value for the
                # supported R/L/C/source/port subset.
                quoted = re.compile(r'"(?:[^"\\]|\\.)*"')
                matches = list(quoted.finditer(raw))
                if matches:
                    match = matches[0]
                    replacement = values[tokens[1]]
                    assert replacement is not None
                    raw = raw[: match.start()] + _quote(replacement) + raw[match.end() :]
        output.append(raw)
    return "\n".join(output) + ("\n" if source_text.endswith(("\n", "\r")) else "")


def import_qucs_file(path: str | Path, *, format: QucsFormat | None = None) -> CircuitDocument:
    source = Path(path)
    text = source.read_text(encoding="utf-8", errors="strict")
    selected = format or ("schematic" if source.suffix.lower() == ".sch" else "netlist")
    if selected == "schematic":
        return parse_qucs_schematic_text(text, artifact=source.name)
    return parse_qucs_netlist_text(text, artifact=source.name)


def export_qucs_file(
    document: CircuitDocument,
    path: str | Path,
    *,
    format: QucsFormat | None = None,
) -> Path:
    selected = format or ("schematic" if document.source_format == "qucs_schematic" else "netlist")
    text = (
        export_qucs_schematic_text(document)
        if selected == "schematic"
        else export_qucs_netlist_text(document)
    )
    target = Path(path)
    target.write_text(text, encoding="utf-8")
    return target


__all__ = [
    "QucsFormat",
    "export_qucs_file",
    "export_qucs_netlist_text",
    "export_qucs_schematic_text",
    "import_qucs_file",
    "parse_qucs_netlist_text",
    "parse_qucs_schematic_text",
]
