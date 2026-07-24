from __future__ import annotations

import pytest

from rf_mcp_common.circuit_ir import CircuitChange
from rf_mcp_common.spice_io import export_spice_text, parse_spice_text

NETLIST = """Voltage divider
V1 in 0 AC 1
R1 in out 1k
R2 out 0 2k
.ac dec 10 10 1Meg
.end
"""


def test_spice_round_trip_preserves_connectivity_and_values() -> None:
    first = parse_spice_text(NETLIST, dialect="ngspice", artifact="divider.cir")
    assert first.is_supported
    assert [component.value for component in first.components if component.kind == "resistor"] == [
        "1k",
        "2k",
    ]

    normalized = export_spice_text(first, preserve_source=False)
    second = parse_spice_text(normalized, dialect="xyce")
    assert second.is_supported
    assert second.connectivity_signature() == first.connectivity_signature()
    assert [component.value for component in second.components if component.kind == "resistor"] == [
        "1k",
        "2k",
    ]


def test_spice_transformation_exports_changed_value_without_topology_change() -> None:
    first = parse_spice_text(NETLIST)
    changed = first.transformed(
        [CircuitChange(path="components.R2.value", before="2k", after="3k")],
        operation="tune",
    )
    reparsed = parse_spice_text(export_spice_text(changed))
    assert reparsed.connectivity_signature() == first.connectivity_signature()
    assert (
        next(component for component in reparsed.components if component.refdes == "R2").value
        == "3k"
    )


def test_spice_enumerates_unsupported_constructs_and_blocks_export() -> None:
    text = """Unsupported
Z1 a b mystery
.control
shell unsafe
.endc
.subckt child x y
R1 x y 1k
.ends
.end
"""
    document = parse_spice_text(text)
    codes = [item.code for item in document.unsupported]
    assert codes == [
        "spice.element",
        "spice.control_command",
        "spice.subcircuit_definition",
    ]
    with pytest.raises(ValueError, match=r"spice\.element"):
        export_spice_text(document)


def test_spice_four_terminal_bjt_connectivity() -> None:
    document = parse_spice_text("BJT substrate\nQ1 c b e sub npnmod\n.end\n")
    assert document.is_supported
    assert document.components[0].pins == {"1": "c", "2": "b", "3": "e", "4": "sub"}
    assert document.components[0].parameters["model"] == "npnmod"
