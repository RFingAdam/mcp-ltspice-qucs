"""Tests for Richards / Kuroda lumped-to-distributed conversion."""

from __future__ import annotations

import pytest

from mcp_qucs_s.microstrip import Substrate
from mcp_qucs_s.richards import lumped_to_distributed


@pytest.fixture
def fr4() -> Substrate:
    return Substrate(er=4.4, h_mm=0.254)


def test_simple_3rd_order_lpf_gives_3_or_more_elements(fr4) -> None:
    components = {"L1": 5e-9, "C2": 2e-12, "L3": 5e-9}
    result = lumped_to_distributed(
        components,
        cutoff_hz=1e9,
        substrate=fr4,
        apply_kuroda=False,
    )
    # Without Kuroda: exactly N elements (one per lumped component)
    assert result["n_elements"] == 3
    # Roles: series_short_stub for L, shunt_open_stub for C
    roles = [e["role"] for e in result["elements"]]
    assert roles == ["series_short_stub", "shunt_open_stub", "series_short_stub"]


def test_kuroda_inserts_connecting_lines(fr4) -> None:
    components = {"L1": 5e-9, "C2": 2e-12, "L3": 5e-9}
    result = lumped_to_distributed(
        components,
        cutoff_hz=1e9,
        substrate=fr4,
        apply_kuroda=True,
    )
    # With Kuroda: original 3 elements + 2 connecting lines = 5
    assert result["n_elements"] == 5
    roles = [e["role"] for e in result["elements"]]
    assert roles.count("connecting_line") == 2


def test_stubs_have_lambda_over_8_at_fc(fr4) -> None:
    components = {"L1": 5e-9, "C2": 2e-12}
    result = lumped_to_distributed(
        components,
        cutoff_hz=1e9,
        substrate=fr4,
        apply_kuroda=False,
    )
    for elt in result["elements"]:
        assert elt["electrical_length_deg"] == 45.0
        assert elt["length_mm"] > 0


def test_inductor_becomes_higher_z_stub(fr4) -> None:
    """An L of higher value gives a higher-Z series stub at fixed fc."""
    res_small = lumped_to_distributed(
        {"L1": 1e-9},
        cutoff_hz=1e9,
        substrate=fr4,
        apply_kuroda=False,
    )
    res_large = lumped_to_distributed(
        {"L1": 10e-9},
        cutoff_hz=1e9,
        substrate=fr4,
        apply_kuroda=False,
    )
    assert res_large["elements"][0]["z0_ohm"] > res_small["elements"][0]["z0_ohm"]


def test_capacitor_becomes_lower_z_stub(fr4) -> None:
    """A larger shunt C gives a lower-Z (wider) stub since Z = 1/(omega C)."""
    res_small = lumped_to_distributed(
        {"C1": 1e-12},
        cutoff_hz=1e9,
        substrate=fr4,
        apply_kuroda=False,
    )
    res_large = lumped_to_distributed(
        {"C1": 10e-12},
        cutoff_hz=1e9,
        substrate=fr4,
        apply_kuroda=False,
    )
    assert res_large["elements"][0]["z0_ohm"] < res_small["elements"][0]["z0_ohm"]


def test_kuroda_emits_explanatory_note(fr4) -> None:
    components = {"L1": 5e-9, "C2": 2e-12}
    result = lumped_to_distributed(
        components,
        cutoff_hz=1e9,
        substrate=fr4,
        apply_kuroda=True,
    )
    assert any("Kuroda" in n for n in result["notes"])
