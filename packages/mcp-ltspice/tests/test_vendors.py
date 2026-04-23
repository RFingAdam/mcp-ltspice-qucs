"""Tests for the vendor model bundles."""

from __future__ import annotations

import pytest

from mcp_ltspice.vendors import (
    find_mosfet_for_application,
    find_opamp_for_application,
    list_bjts,
    list_diodes,
    list_mosfets,
    list_opamps,
    list_references,
    lookup_bjt,
    lookup_diode,
    lookup_mosfet,
    lookup_opamp,
    lookup_reference,
)

# ---- Op-amps ------------------------------------------------------------


def test_opamp_catalog_has_common_parts() -> None:
    parts = list_opamps()
    for p in ("LM358", "OPA350", "OPA827", "MCP6004", "AD8629"):
        assert p in parts


def test_lookup_opamp_returns_full_data() -> None:
    op = lookup_opamp("OPA350")
    assert op.gbw_mhz == 38.0
    assert op.rail_to_rail_input is True
    assert op.rail_to_rail_output is True


def test_lookup_opamp_unknown_raises() -> None:
    with pytest.raises(ValueError):
        lookup_opamp("NotAnOpAmp42")


def test_find_opamp_filters_by_gbw() -> None:
    high_speed = find_opamp_for_application(min_gbw_mhz=50)
    assert all(o.gbw_mhz >= 50 for o in high_speed)
    # Should include high-speed parts
    assert any(o.part_number == "THS3491" for o in high_speed)


def test_find_opamp_filters_by_noise() -> None:
    quiet = find_opamp_for_application(max_input_noise_nv_per_rthz=5)
    assert all(o.input_noise_nv_per_rthz <= 5 for o in quiet)


def test_find_opamp_filters_by_rrio() -> None:
    rrio = find_opamp_for_application(rail_to_rail_input=True, rail_to_rail_output=True)
    assert all(o.rail_to_rail_input and o.rail_to_rail_output for o in rrio)


# ---- MOSFETs ------------------------------------------------------------


def test_mosfet_catalog_has_common_parts() -> None:
    parts = list_mosfets()
    assert "BSS138" in parts
    assert "AO3400A" in parts


def test_lookup_mosfet_returns_full_data() -> None:
    m = lookup_mosfet("BSS138")
    assert m.polarity == "N"
    assert m.vds_max_v == 50


def test_find_mosfet_filters_by_polarity_and_id() -> None:
    high_current = find_mosfet_for_application(polarity="N", min_id_a=50)
    assert all(m.polarity == "N" and m.id_continuous_a >= 50 for m in high_current)


def test_find_mosfet_sorted_by_rds_on() -> None:
    """Default sort_by='rds_on_max_mohm' returns lowest-loss first."""
    candidates = find_mosfet_for_application(polarity="N", min_vds_v=20)
    rds_values = [m.rds_on_max_mohm for m in candidates]
    assert rds_values == sorted(rds_values)


def test_find_mosfet_p_polarity() -> None:
    p_fets = find_mosfet_for_application(polarity="P")
    assert all(m.polarity == "P" for m in p_fets)
    assert any(m.part_number == "AO3401A" for m in p_fets)


# ---- BJTs ---------------------------------------------------------------


def test_bjt_catalog_includes_2n3904() -> None:
    assert "2N3904" in list_bjts()


def test_lookup_bjt() -> None:
    b = lookup_bjt("2N3904")
    assert b.polarity == "NPN"
    assert b.hfe_typ == 200


# ---- Diodes -------------------------------------------------------------


def test_diode_catalog_includes_1n4148() -> None:
    assert "1N4148" in list_diodes()


def test_lookup_diode_kind() -> None:
    d = lookup_diode("BAT54")
    assert d.kind == "schottky"
    assert d.vf_typ_v < 0.5  # low-Vf Schottky


def test_lookup_tvs() -> None:
    t = lookup_diode("SMAJ5.0A")
    assert t.kind == "tvs"


# ---- References ---------------------------------------------------------


def test_reference_catalog_includes_common_parts() -> None:
    parts = list_references()
    assert "REF3025" in parts
    assert "ADR4525" in parts


def test_lookup_reference_precision() -> None:
    """ADR4525 is ultra-precision (0.02% accuracy, 2 ppm/°C)."""
    r = lookup_reference("ADR4525")
    assert r.initial_accuracy_pct <= 0.05
    assert r.tempco_ppm_per_c <= 5


def test_lookup_unknown_raises() -> None:
    with pytest.raises(ValueError):
        lookup_reference("NotAReference")
    with pytest.raises(ValueError):
        lookup_bjt("NotABJT")
    with pytest.raises(ValueError):
        lookup_diode("NotADiode")
    with pytest.raises(ValueError):
        lookup_mosfet("NotAMOSFET")
