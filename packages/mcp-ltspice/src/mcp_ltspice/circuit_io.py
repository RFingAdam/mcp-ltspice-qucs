"""LTspice schematic import/export through :class:`CircuitDocument`.

Electrical connectivity is derived from symbol pins, wire geometry, and net
flags using the same union-find rules as LTspice netlisting.  Unknown symbol
pin geometry is reported explicitly and blocks compilation; it is never
replaced with a guessed two-terminal device.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from mcp_ltspice.asc_io import read_asc_text
from mcp_ltspice.asc_netlist import PIN_OFFSETS, SPICE_PREFIX, AscSchematic, build_nodes, parse_asc
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

_ASC_KIND = {
    "res": "resistor",
    "ind": "inductor",
    "ind2": "inductor",
    "cap": "capacitor",
    "polcap": "capacitor",
    "voltage": "voltage_source",
    "current": "current_source",
}
_ROTATION = {"R0": (0, False), "R90": (90, False), "R180": (180, False), "R270": (270, False)}
_ANALYSIS_PREFIX = {
    ".op": "op",
    ".dc": "dc",
    ".ac": "ac",
    ".tran": "transient",
    ".noise": "noise",
}


def _line_for_symbol(source_lines: list[str], ordinal: int) -> int | None:
    seen = 0
    for index, line in enumerate(source_lines, start=1):
        if line.lstrip().startswith("SYMBOL "):
            if seen == ordinal:
                return index
            seen += 1
    return None


def import_ltspice_asc(path: str | Path) -> CircuitDocument:
    """Import a supported LTspice ``.asc`` without topology inference."""
    source = Path(path)
    decoded = read_asc_text(source)
    text = decoded.text
    lines = text.splitlines()
    schematic = parse_asc(source)
    unsupported: list[UnsupportedConstruct] = []

    known_symbols = []
    for ordinal, symbol in enumerate(schematic.symbols):
        symbol_line = _line_for_symbol(lines, ordinal)
        if symbol.kind not in PIN_OFFSETS or symbol.kind not in SPICE_PREFIX:
            unsupported.append(
                UnsupportedConstruct(
                    code="ltspice.symbol_geometry",
                    message=(
                        f"symbol {symbol.kind!r}"
                        + (f" ({symbol.inst})" if symbol.inst else "")
                        + " has no registered pin geometry"
                    ),
                    raw=lines[symbol_line - 1] if symbol_line else None,
                    location=SourceLocation(artifact=source.name, line=symbol_line),
                )
            )
            continue
        if symbol.inst is None:
            unsupported.append(
                UnsupportedConstruct(
                    code="ltspice.missing_instname",
                    message=f"symbol {symbol.kind!r} has no InstName attribute",
                    location=SourceLocation(artifact=source.name, line=symbol_line),
                )
            )
            continue
        known_symbols.append(symbol)

    for line_number, source_line in enumerate(lines, start=1):
        stripped = source_line.strip()
        if stripped.startswith("WIRE "):
            fields = stripped.split()
            if len(fields) >= 5 and fields[1] != fields[3] and fields[2] != fields[4]:
                unsupported.append(
                    UnsupportedConstruct(
                        code="ltspice.diagonal_wire",
                        message="diagonal wire connectivity is not supported",
                        raw=stripped,
                        location=SourceLocation(artifact=source.name, line=line_number),
                    )
                )
        elif stripped.startswith(
            ("SHEET ", "Version ", "SYMBOL ", "SYMATTR ", "WINDOW ", "FLAG ", "TEXT ")
        ):
            continue
        elif stripped.startswith(("LINE ", "RECTANGLE ", "CIRCLE ", "ARC ")):
            unsupported.append(
                UnsupportedConstruct(
                    code="ltspice.graphic",
                    message="drawing primitive is preserved but has no electrical semantics",
                    raw=stripped,
                    location=SourceLocation(artifact=source.name, line=line_number),
                    severity="warning",
                )
            )
        elif stripped:
            unsupported.append(
                UnsupportedConstruct(
                    code="ltspice.record",
                    message="unrecognized ASC record",
                    raw=stripped,
                    location=SourceLocation(artifact=source.name, line=line_number),
                )
            )

    supported_schematic = AscSchematic(
        symbols=known_symbols,
        wires=schematic.wires,
        flags=schematic.flags,
        directives=schematic.directives,
    )
    try:
        point_nodes = build_nodes(supported_schematic)
    except (NotImplementedError, ValueError) as exc:
        unsupported.append(
            UnsupportedConstruct(
                code="ltspice.connectivity",
                message=str(exc),
                location=SourceLocation(artifact=source.name),
            )
        )
        point_nodes = {}

    components: list[CircuitComponent] = []
    nets: set[str] = set(point_nodes.values())
    for ordinal, symbol in enumerate(schematic.symbols):
        if symbol not in known_symbols or symbol.inst is None:
            continue
        symbol_line = _line_for_symbol(lines, ordinal)
        pins = symbol.pins()
        try:
            pin_map = {str(index): point_nodes[point] for index, point in enumerate(pins, start=1)}
        except KeyError:
            unsupported.append(
                UnsupportedConstruct(
                    code="ltspice.unresolved_pin",
                    message=f"{symbol.inst} has a pin that could not be assigned to a net",
                    location=SourceLocation(artifact=source.name, line=symbol_line),
                )
            )
            continue
        angle, mirrored = _ROTATION.get(
            symbol.rot.replace("M", "R", 1),
            (None, symbol.rot.startswith("M")),
        )
        components.append(
            CircuitComponent(
                refdes=symbol.inst,
                kind=_ASC_KIND[symbol.kind],
                pins=pin_map,
                value=symbol.value,
                attributes=dict(symbol.attrs),
                geometry=CircuitGeometry(
                    x=symbol.x,
                    y=symbol.y,
                    rotation_deg=angle,
                    mirrored=mirrored,
                    source_token=symbol.kind,
                ),
                source=SourceLocation(artifact=source.name, line=symbol_line),
            )
        )
        nets.update(pin_map.values())

    directives = [
        CircuitDirective(
            text=directive,
            dialect="ltspice",
            source=SourceLocation(artifact=source.name),
        )
        for directive in schematic.directives
    ]
    analyses: list[CircuitAnalysis] = []
    for index, directive in enumerate(schematic.directives, start=1):
        keyword = directive.lower().split(maxsplit=1)[0]
        if keyword in _ANALYSIS_PREFIX:
            analyses.append(
                CircuitAnalysis(
                    id=f"{_ANALYSIS_PREFIX[keyword]}_{index}",
                    kind=_ANALYSIS_PREFIX[keyword],  # type: ignore[arg-type]
                    parameters={"statement": directive},
                )
            )

    ports: list[CircuitPort] = []
    for _, _, label in schematic.flags:
        match = re.fullmatch(r"[pP](\d+)", label)
        if match and label in nets and "0" in nets:
            ports.append(
                CircuitPort(
                    name=label,
                    number=int(match.group(1)),
                    positive_net=label,
                    negative_net="0",
                )
            )

    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    metadata: dict[str, Any] = {
        "source_text": text,
        "encoding": decoded.encoding,
        "newline": decoded.newline,
        "wires": [list(wire) for wire in schematic.wires],
        "flags": [list(flag) for flag in schematic.flags],
    }
    document = CircuitDocument(
        document_id=f"ltasc-{source_sha[:16]}",
        title=source.stem,
        source_format="ltspice_asc",
        source_dialect="ltspice",
        nodes=node_list(nets),
        components=components,
        ports=ports,
        analyses=analyses,
        directives=directives,
        provenance=CircuitProvenance(
            source_artifact=source.name,
            source_sha256=source_sha,
            importer="mcp_ltspice.circuit_io.import_ltspice_asc",
        ),
        unsupported=unsupported,
        metadata=metadata,
    )
    document.metadata["imported_fingerprint"] = document.electrical_fingerprint()
    return document


def _rewrite_values(document: CircuitDocument, source_text: str) -> str:
    """Rewrite only Value attributes while preserving all ASC geometry."""
    desired = {component.refdes: component.value for component in document.components}
    output: list[str] = []
    current_refdes: str | None = None
    pending_symbol = False
    value_seen = False

    def finish_symbol() -> None:
        nonlocal value_seen
        if (
            pending_symbol
            and current_refdes
            and not value_seen
            and desired.get(current_refdes) is not None
        ):
            output.append(f"SYMATTR Value {desired[current_refdes]}")
        value_seen = False

    for line in source_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("SYMBOL "):
            finish_symbol()
            current_refdes = None
            pending_symbol = True
        elif stripped.startswith("SYMATTR InstName ") and pending_symbol:
            current_refdes = stripped.split(maxsplit=2)[2]
        elif stripped.startswith("SYMATTR Value ") and pending_symbol and current_refdes in desired:
            value_seen = True
            replacement = desired[current_refdes]
            line = f"SYMATTR Value {replacement}" if replacement is not None else line
        elif not stripped.startswith(("SYMATTR ", "WINDOW ")) and pending_symbol:
            finish_symbol()
            pending_symbol = False
            current_refdes = None
        output.append(line)
    finish_symbol()
    return "\n".join(output) + ("\n" if source_text.endswith(("\n", "\r")) else "")


def export_ltspice_asc(document: CircuitDocument, path: str | Path) -> Path:
    """Export an imported ASC while preserving its topology and drawing."""
    document.require_supported()
    if document.source_format != "ltspice_asc":
        raise ValueError("LTspice ASC export currently requires an imported LTspice schematic")
    source_text = document.metadata.get("source_text")
    if not isinstance(source_text, str):
        raise ValueError("LTspice document has no preserved source geometry")
    target = Path(path)
    text = (
        source_text
        if document.metadata.get("imported_fingerprint") == document.electrical_fingerprint()
        else _rewrite_values(document, source_text)
    )
    encoding = str(document.metadata.get("encoding", "utf-8"))
    target.write_text(text, encoding=encoding, newline="")
    return target


__all__ = ["export_ltspice_asc", "import_ltspice_asc"]
