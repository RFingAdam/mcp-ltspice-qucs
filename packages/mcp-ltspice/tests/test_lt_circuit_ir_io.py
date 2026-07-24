from __future__ import annotations

from mcp_ltspice.asc_io import generate_lpf_asc
from mcp_ltspice.circuit_io import export_ltspice_asc, import_ltspice_asc
from rf_mcp_common.circuit_ir import CircuitChange


def test_ltspice_asc_round_trip_preserves_connectivity_and_values(tmp_path) -> None:
    source = generate_lpf_asc(
        {"L1": 8.2e-9, "C2": 3.3e-12, "L3": 8.2e-9},
        tmp_path / "filter.asc",
    )
    first = import_ltspice_asc(source)
    assert first.is_supported, first.unsupported

    target = export_ltspice_asc(first, tmp_path / "roundtrip.asc")
    second = import_ltspice_asc(target)
    assert second.connectivity_signature() == first.connectivity_signature()
    assert {component.refdes: component.value for component in second.components} == {
        component.refdes: component.value for component in first.components
    }


def test_ltspice_asc_value_change_preserves_geometry_and_connectivity(tmp_path) -> None:
    source = generate_lpf_asc({"L1": 8.2e-9}, tmp_path / "filter.asc")
    first = import_ltspice_asc(source)
    original = next(component for component in first.components if component.refdes == "L1")
    changed = first.transformed(
        [
            CircuitChange(
                path="components.L1.value",
                before=original.value,
                after="10n",
            )
        ],
        operation="tune",
    )
    target = export_ltspice_asc(changed, tmp_path / "changed.asc")
    second = import_ltspice_asc(target)
    assert second.connectivity_signature() == first.connectivity_signature()
    assert (
        next(component for component in second.components if component.refdes == "L1").value
        == "10n"
    )


def test_ltspice_unknown_symbol_is_complete_blocking_diagnostic(tmp_path) -> None:
    path = tmp_path / "unknown.asc"
    path.write_text(
        "Version 4\nSHEET 1 320 200\nSYMBOL mystery 100 100 R0\nSYMATTR InstName X1\n",
        encoding="utf-8",
    )
    document = import_ltspice_asc(path)
    assert not document.is_supported
    assert document.unsupported[0].code == "ltspice.symbol_geometry"
    assert document.unsupported[0].location
    assert document.unsupported[0].location.line == 3
