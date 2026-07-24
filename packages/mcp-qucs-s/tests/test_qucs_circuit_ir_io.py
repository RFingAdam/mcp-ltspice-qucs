from __future__ import annotations

import pytest

from mcp_qucs_s.circuit_io import (
    export_qucs_netlist_text,
    export_qucs_schematic_text,
    parse_qucs_netlist_text,
    parse_qucs_schematic_text,
)
from rf_mcp_common.circuit_ir import CircuitChange

NETLIST = """# divider
Pac:P1 in gnd Num="1" Z="50 Ohm" P="0 dBm" f="1 GHz"
R:R1 in out R="50 Ohm"
C:C1 out gnd C="2 pF"
Pac:P2 out gnd Num="2" Z="50 Ohm" P="0 dBm" f="1 GHz"
.SP:SP1 Type="log" Start="1 MHz" Stop="1 GHz" Points="101"
"""

SCHEMATIC = """<Qucs Schematic 0.0.19>
<Properties>
</Properties>
<Symbol>
</Symbol>
<Components>
<R R1 1 170 90 -26 15 0 0 "50 Ohm" 1>
<C C1 1 230 150 -26 15 0 1 "2 pF" 1>
<GND * 1 230 210 0 0 0 0>
</Components>
<Wires>
<200 90 230 90 "" 0 0 0 "">
<230 90 230 120 "out" 230 90 0 "">
<230 180 230 180 "" 0 0 0 "">
</Wires>
<Diagrams>
</Diagrams>
<Paintings>
</Paintings>
"""


def test_qucs_netlist_round_trip_connectivity_values_and_ports() -> None:
    first = parse_qucs_netlist_text(NETLIST, artifact="divider.net")
    assert first.is_supported, first.unsupported
    assert len(first.ports) == 2
    second = parse_qucs_netlist_text(export_qucs_netlist_text(first, preserve_source=False))
    assert second.connectivity_signature() == first.connectivity_signature()
    assert (
        next(component for component in second.components if component.refdes == "C1").value
        == "2 pF"
    )


def test_qucs_schematic_round_trip_and_value_rewrite() -> None:
    first = parse_qucs_schematic_text(SCHEMATIC, artifact="divider.sch")
    assert first.is_supported, first.unsupported
    changed = first.transformed(
        [CircuitChange(path="components.R1.value", before="50 Ohm", after="75 Ohm")],
        operation="tune",
    )
    exported = export_qucs_schematic_text(changed)
    second = parse_qucs_schematic_text(exported)
    assert second.connectivity_signature() == first.connectivity_signature()
    assert (
        next(component for component in second.components if component.refdes == "R1").value
        == "75 Ohm"
    )


def test_qucs_schematic_unknown_component_blocks_export() -> None:
    text = SCHEMATIC.replace(
        '<R R1 1 170 90 -26 15 0 0 "50 Ohm" 1>',
        '<Mystery X1 1 170 90 -26 15 0 0 "x" 1>',
    )
    document = parse_qucs_schematic_text(text)
    assert [item.code for item in document.unsupported] == ["qucs.schematic_component"]
    with pytest.raises(ValueError, match=r"qucs\.schematic_component"):
        export_qucs_schematic_text(document)
