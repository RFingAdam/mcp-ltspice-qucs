"""Dialect-aware SPICE netlist import/export for the shared circuit IR.

This is intentionally a bounded parser, not a simulator.  It preserves every
directive and reports every statement whose connectivity cannot be represented.
Supported primitive connectivity round-trips across ngspice, LTspice, and Xyce
dialects without relying on reference-designator ordering.
"""

from __future__ import annotations

import hashlib
import re
import shlex
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from rf_mcp_common.circuit_ir import (
    CircuitAnalysis,
    CircuitComponent,
    CircuitDependency,
    CircuitDirective,
    CircuitDocument,
    CircuitPort,
    CircuitProvenance,
    SourceLocation,
    UnsupportedConstruct,
    node_list,
)

SpiceDialect = Literal["spice", "ngspice", "ltspice", "xyce"]

_FIXED_NODE_COUNTS: dict[str, int] = {
    "R": 2,
    "C": 2,
    "L": 2,
    "V": 2,
    "I": 2,
    "D": 2,
    "Q": 3,
    "J": 3,
    "M": 4,
    "E": 4,
    "G": 4,
    "F": 2,
    "H": 2,
    "T": 4,
    "W": 2,
    "S": 4,
    "B": 2,
}
_KINDS = {
    "R": "resistor",
    "C": "capacitor",
    "L": "inductor",
    "V": "voltage_source",
    "I": "current_source",
    "D": "diode",
    "Q": "bjt",
    "J": "jfet",
    "M": "mosfet",
    "E": "vcvs",
    "G": "vccs",
    "F": "cccs",
    "H": "ccvs",
    "T": "transmission_line",
    "W": "current_switch",
    "S": "voltage_switch",
    "B": "behavioral_source",
    "X": "subcircuit",
}
_ANALYSES = {
    ".op": "op",
    ".dc": "dc",
    ".ac": "ac",
    ".tran": "transient",
    ".noise": "noise",
    ".hb": "harmonic_balance",
}


def _logical_lines(text: str) -> Iterable[tuple[int, str]]:
    current: str | None = None
    start = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith("+") and current is not None:
            current += " " + stripped[1:].strip()
            continue
        if current is not None:
            yield start, current
        current = raw
        start = number
    if current is not None:
        yield start, current


def _tokens(statement: str) -> list[str]:
    lexer = shlex.shlex(statement, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _parameter_tokens(tokens: list[str]) -> tuple[str | None, dict[str, str]]:
    positional: list[str] = []
    parameters: dict[str, str] = {}
    for token in tokens:
        if "=" in token:
            key, value = token.split("=", 1)
            parameters[key] = value
        else:
            positional.append(token)
    return (" ".join(positional) if positional else None), parameters


def _component_from_tokens(
    tokens: list[str],
    *,
    line: int,
    artifact: str | None,
) -> CircuitComponent:
    refdes = tokens[0]
    prefix = refdes[0].upper()
    if prefix == "X":
        if len(tokens) < 4:
            raise ValueError(
                "subcircuit instance requires at least two nodes and a subcircuit name"
            )
        parameter_start = next(
            (index for index, token in enumerate(tokens[1:], start=1) if "=" in token),
            len(tokens),
        )
        positional = tokens[1:parameter_start]
        if len(positional) < 3:
            raise ValueError("subcircuit instance has no model/subcircuit token")
        nets, model_name = positional[:-1], positional[-1]
        _, parsed_parameters = _parameter_tokens(tokens[parameter_start:])
        parameters: dict[str, str | float | int | bool] = {
            "subcircuit": model_name,
            **parsed_parameters,
        }
    elif prefix == "Q":
        parameter_start = next(
            (index for index, token in enumerate(tokens[1:], start=1) if "=" in token),
            len(tokens),
        )
        positional = tokens[1:parameter_start]
        if len(positional) not in {4, 5}:
            raise ValueError("BJT requires three or four nodes followed by a model name")
        nets, model_name = positional[:-1], positional[-1]
        _, parsed_parameters = _parameter_tokens(tokens[parameter_start:])
        parameters = {"model": model_name, **parsed_parameters}
    else:
        node_count = _FIXED_NODE_COUNTS[prefix]
        if len(tokens) < node_count + 1:
            raise ValueError(f"{prefix} element requires {node_count} connectivity tokens")
        nets = tokens[1 : 1 + node_count]
        tail_value, parsed_parameters = _parameter_tokens(tokens[1 + node_count :])
        parameters = dict(parsed_parameters)
        if tail_value is not None:
            parameters = {
                "source_expression" if prefix in {"V", "I", "B"} else "tail": tail_value,
                **parameters,
            }
    pins = {str(index + 1): net for index, net in enumerate(nets)}
    value: str | None = None
    if prefix in {"R", "C", "L"}:
        parameter_tail = parameters.pop("tail", None)
        value = str(parameter_tail) if parameter_tail is not None else None
    elif prefix in {"D", "Q", "J", "M", "S", "W"}:
        parameter_tail = parameters.pop("tail", None)
        if parameter_tail is not None:
            pieces = str(parameter_tail).split(maxsplit=1)
            parameters["model"] = pieces[0]
            if len(pieces) > 1:
                parameters["tail"] = pieces[1]
    return CircuitComponent(
        refdes=refdes,
        kind=_KINDS[prefix],
        pins=pins,
        value=value,
        parameters=parameters,
        source=SourceLocation(artifact=artifact, line=line),
    )


def parse_spice_text(
    text: str,
    *,
    dialect: SpiceDialect = "spice",
    artifact: str | None = None,
) -> CircuitDocument:
    """Parse supported SPICE constructs and enumerate all unsupported ones."""
    components: list[CircuitComponent] = []
    directives: list[CircuitDirective] = []
    analyses: list[CircuitAnalysis] = []
    dependencies: list[CircuitDependency] = []
    unsupported: list[UnsupportedConstruct] = []
    nets: set[str] = set()
    title: str | None = None
    first_statement = True
    in_control = False
    in_subcircuit = False

    for line_number, raw in _logical_lines(text):
        statement = raw.strip()
        if not statement or statement.startswith(("*", ";", "$")):
            continue
        location = SourceLocation(artifact=artifact, line=line_number)
        if first_statement and not statement.startswith("."):
            # A title line does not start with a legal element prefix plus a
            # complete node list in most netlists. Prefer parsing a structurally
            # valid element; otherwise retain it as the traditional title.
            first_tokens = _tokens(statement)
            first_token = first_tokens[0] if first_tokens else ""
            prefix = first_token[0].upper() if first_token else ""
            minimum_tokens = (
                4
                if prefix == "X"
                else 5
                if prefix == "Q"
                else 1 + _FIXED_NODE_COUNTS.get(prefix, 10_000)
            )
            if not first_token or len(first_tokens) < minimum_tokens:
                title = statement
                first_statement = False
                continue
        first_statement = False
        lower = statement.lower()
        if lower.startswith(".subckt"):
            in_subcircuit = True
            directives.append(CircuitDirective(text=statement, dialect=dialect, source=location))
            unsupported.append(
                UnsupportedConstruct(
                    code="spice.subcircuit_definition",
                    message=(
                        "subcircuit definitions are preserved but hierarchical body "
                        "semantics are not imported"
                    ),
                    raw=statement,
                    location=location,
                )
            )
            continue
        if lower.startswith(".ends"):
            in_subcircuit = False
            directives.append(CircuitDirective(text=statement, dialect=dialect, source=location))
            continue
        if in_subcircuit:
            directives.append(CircuitDirective(text=statement, dialect=dialect, source=location))
            continue
        if lower == ".control":
            in_control = True
            directives.append(CircuitDirective(text=statement, dialect=dialect, source=location))
            continue
        if lower == ".endc":
            in_control = False
            directives.append(CircuitDirective(text=statement, dialect=dialect, source=location))
            continue
        if in_control:
            unsupported.append(
                UnsupportedConstruct(
                    code="spice.control_command",
                    message="interactive control commands are preserved but not compiled through the IR",
                    raw=statement,
                    location=location,
                )
            )
            directives.append(CircuitDirective(text=statement, dialect=dialect, source=location))
            continue
        if statement.startswith("."):
            directives.append(CircuitDirective(text=statement, dialect=dialect, source=location))
            keyword = lower.split(maxsplit=1)[0]
            if keyword in _ANALYSES:
                analyses.append(
                    CircuitAnalysis(
                        id=f"{_ANALYSES[keyword]}_{len(analyses) + 1}",
                        kind=_ANALYSES[keyword],  # type: ignore[arg-type]
                        parameters={"statement": statement},
                    )
                )
            elif keyword in {".include", ".inc", ".lib"}:
                parts = _tokens(statement)
                if len(parts) >= 2:
                    dependencies.append(
                        CircuitDependency(
                            reference=parts[1],
                            kind="library" if keyword == ".lib" else "include",
                        )
                    )
            elif keyword not in {
                ".end",
                ".model",
                ".param",
                ".temp",
                ".options",
                ".save",
                ".print",
                ".plot",
                ".probe",
                ".nodeset",
                ".ic",
                ".global",
                ".func",
                ".meas",
                ".measure",
            }:
                unsupported.append(
                    UnsupportedConstruct(
                        code="spice.directive",
                        message=f"directive {keyword!r} is preserved but has no normalized semantics",
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
                    code="spice.tokenization",
                    message=str(exc),
                    raw=statement,
                    location=location,
                )
            )
            continue
        if not tokens:
            continue
        prefix = tokens[0][0].upper()
        if prefix not in {*_FIXED_NODE_COUNTS, "X"}:
            unsupported.append(
                UnsupportedConstruct(
                    code="spice.element",
                    message=f"element prefix {prefix!r} is not supported",
                    raw=statement,
                    location=location,
                )
            )
            continue
        try:
            component = _component_from_tokens(tokens, line=line_number, artifact=artifact)
        except ValueError as exc:
            unsupported.append(
                UnsupportedConstruct(
                    code="spice.element_shape",
                    message=str(exc),
                    raw=statement,
                    location=location,
                )
            )
            continue
        components.append(component)
        nets.update(component.pins.values())

    source_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    metadata = {"source_text": text}
    document = CircuitDocument(
        document_id=f"spice-{source_sha[:16]}",
        title=title,
        source_format="spice",
        source_dialect=dialect,
        nodes=node_list(nets),
        components=components,
        analyses=analyses,
        directives=directives,
        dependencies=dependencies,
        provenance=CircuitProvenance(
            source_artifact=artifact,
            source_sha256=source_sha,
            importer="rf_mcp_common.spice_io.parse_spice_text",
        ),
        unsupported=unsupported,
        metadata=metadata,
    )
    document.metadata["imported_fingerprint"] = document.electrical_fingerprint()
    return document


def parse_spice_file(path: str | Path, *, dialect: SpiceDialect = "spice") -> CircuitDocument:
    source = Path(path)
    return parse_spice_text(
        source.read_text(encoding="utf-8", errors="replace"),
        dialect=dialect,
        artifact=source.name,
    )


def _pin_sort_key(item: tuple[str, str]) -> tuple[int, int | str]:
    try:
        return (0, int(item[0]))
    except ValueError:
        return (1, item[0])


def _ordered_nets(component: CircuitComponent) -> list[str]:
    return [value for _, value in sorted(component.pins.items(), key=_pin_sort_key)]


def _model_ordered_nets(component: CircuitComponent) -> list[str]:
    """Order circuit nets by the selected model's explicit pin map."""
    model = component.model
    assert model is not None
    positions = sorted(model.pin_map.values())
    if positions != list(range(1, len(model.pin_map) + 1)):
        raise ValueError(
            f"{component.refdes}: model pin positions must be contiguous starting at 1"
        )
    if all(role in component.pins for role in model.pin_map):
        by_position = {position: component.pins[role] for role, position in model.pin_map.items()}
    elif all(str(position) in component.pins for position in positions):
        by_position = {position: component.pins[str(position)] for position in positions}
    else:
        raise ValueError(
            f"{component.refdes}: circuit pins cannot be mapped to model roles "
            f"{sorted(model.pin_map)} or positions {positions}"
        )
    return [by_position[position] for position in positions]


def _safe_identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value)


def _numeric_component_value(value: str | float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (float, int)):
        return float(value)
    match = re.fullmatch(
        r"\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*([A-Za-zµ]*)\s*",
        value,
    )
    if match is None:
        raise ValueError(f"modeled component value {value!r} is not numeric")
    scales = {
        "": 1.0,
        "f": 1e-15,
        "p": 1e-12,
        "n": 1e-9,
        "u": 1e-6,
        "µ": 1e-6,
        "m": 1e-3,
        "k": 1e3,
        "meg": 1e6,
        "g": 1e9,
        "t": 1e12,
    }
    suffix = match.group(2).lower()
    if suffix not in scales:
        raise ValueError(f"unknown engineering suffix {suffix!r}")
    return float(match.group(1)) * scales[suffix]


def _instance_parameter_tokens(component: CircuitComponent) -> list[str]:
    consumed = {"subcircuit", "model", "source_expression", "tail"}
    return [f"{key}={value}" for key, value in component.parameters.items() if key not in consumed]


def _render_model_component(
    component: CircuitComponent,
) -> tuple[str, list[str], set[str]]:
    model = component.model
    assert model is not None
    nets = _model_ordered_nets(component)
    if model.model_kind == "subckt":
        if not model.source_path or not model.subcircuit_name:
            raise ValueError(
                f"{component.refdes}: subckt model requires source_path and subcircuit_name"
            )
        return (
            " ".join(
                [
                    f"X{_safe_identifier(component.refdes)}",
                    *nets,
                    model.subcircuit_name,
                    *_instance_parameter_tokens(component),
                ]
            ),
            [],
            {model.source_path},
        )
    if model.model_kind == "model":
        if not model.source_path or not model.subcircuit_name:
            raise ValueError(
                f"{component.refdes}: primitive model requires source_path and model name"
            )
        return (
            " ".join(
                [
                    component.refdes,
                    *nets,
                    model.subcircuit_name,
                    *_instance_parameter_tokens(component),
                ]
            ),
            [],
            {model.source_path},
        )
    if len(nets) != 2:
        raise ValueError(
            f"{component.refdes}: reduced passive model requires exactly two circuit pins"
        )
    parameters = model.electrical_parameters
    name = f"RFMC_{_safe_identifier(component.refdes)}"
    resistance = max(float(parameters.get("Rs_ohm", 0.0)), 1e-12)
    if {"L_h", "Cp_f"} <= parameters.keys():
        inductance = _numeric_component_value(component.value)
        if inductance is None:
            inductance = parameters["L_h"]
        definitions = [
            f".SUBCKT {name} p n",
            f"Rloss p x {resistance:.12g}",
            f"Lmain x n {inductance:.12g}",
            f"Cpar p n {parameters['Cp_f']:.12g}",
            f".ENDS {name}",
        ]
    elif {"C_f", "Ls_h"} <= parameters.keys():
        capacitance = _numeric_component_value(component.value)
        if capacitance is None:
            capacitance = parameters["C_f"]
        definitions = [
            f".SUBCKT {name} p n",
            f"Rloss p x {resistance:.12g}",
            f"Lpar x y {parameters['Ls_h']:.12g}",
            f"Cmain y n {capacitance:.12g}",
            f".ENDS {name}",
        ]
    else:
        raise ValueError(
            f"{component.refdes}: {model.model_kind} has no complete L/C reduction; "
            "provide L_h/Cp_f/Rs_ohm or C_f/Ls_h/Rs_ohm"
        )
    return f"X{_safe_identifier(component.refdes)} {nets[0]} {nets[1]} {name}", definitions, set()


def _render_component(component: CircuitComponent) -> str:
    prefix = component.refdes[0].upper()
    ordered_nets = _ordered_nets(component)
    tail: list[str] = []
    if prefix == "X":
        subcircuit = component.parameters.get("subcircuit")
        if not subcircuit:
            raise ValueError(f"{component.refdes}: missing subcircuit parameter")
        tail.append(str(subcircuit))
    elif component.value is not None:
        tail.append(str(component.value))
    elif "model" in component.parameters:
        tail.append(str(component.parameters["model"]))
    elif "source_expression" in component.parameters:
        tail.append(str(component.parameters["source_expression"]))
    elif "tail" in component.parameters:
        tail.append(str(component.parameters["tail"]))
    consumed = {"subcircuit", "model", "source_expression", "tail"}
    tail.extend(
        f"{key}={value}" for key, value in component.parameters.items() if key not in consumed
    )
    return " ".join([component.refdes, *ordered_nets, *tail]).rstrip()


def export_spice_text(
    document: CircuitDocument,
    *,
    dialect: SpiceDialect | None = None,
    preserve_source: bool = True,
) -> str:
    """Export a supported document to normalized SPICE syntax.

    Unchanged SPICE imports are returned byte-for-byte when
    ``preserve_source`` is true.  Transformed documents are deterministically
    regenerated from explicit connectivity.
    """
    document.require_supported()
    if (
        preserve_source
        and document.source_format == "spice"
        and isinstance(document.metadata.get("source_text"), str)
        and document.metadata.get("imported_fingerprint") == document.electrical_fingerprint()
    ):
        return str(document.metadata["source_text"])
    selected = dialect or (
        document.source_dialect
        if document.source_dialect in {"spice", "ngspice", "ltspice", "xyce"}
        else "spice"
    )
    lines = [document.title or f"* CircuitDocument {document.document_id} ({selected})"]
    definitions: list[str] = []
    includes: set[str] = set()
    body: list[str] = []
    for component in document.components:
        if component.model is None:
            body.append(_render_component(component))
            continue
        instance, model_definitions, model_includes = _render_model_component(component)
        body.append(instance)
        definitions.extend(model_definitions)
        includes.update(model_includes)
    lines.extend(f'.include "{path}"' for path in sorted(includes))
    lines.extend(definitions)
    lines.extend(body)
    lines.extend(
        directive.text for directive in document.directives if directive.text.lower() != ".end"
    )
    lines.append(".end")
    return "\n".join(lines) + "\n"


def export_spice_file(
    document: CircuitDocument,
    path: str | Path,
    *,
    dialect: SpiceDialect | None = None,
    preserve_source: bool = True,
) -> Path:
    target = Path(path)
    target.write_text(
        export_spice_text(document, dialect=dialect, preserve_source=preserve_source),
        encoding="utf-8",
    )
    return target


def infer_ports(
    document: CircuitDocument, names: tuple[str, ...] = ("p1", "p2")
) -> CircuitDocument:
    """Return a copy with named nets represented as external ports."""
    known = {node.id for node in document.nodes}
    ports = [
        CircuitPort(name=name, number=index, positive_net=name, negative_net="0")
        for index, name in enumerate(names, start=1)
        if name in known and "0" in known
    ]
    return document.model_copy(update={"ports": ports}, deep=True)


__all__ = [
    "SpiceDialect",
    "export_spice_file",
    "export_spice_text",
    "infer_ports",
    "parse_spice_file",
    "parse_spice_text",
]
