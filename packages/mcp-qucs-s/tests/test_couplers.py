"""Tests for directional coupler synthesis."""

from __future__ import annotations

import math

import pytest

from mcp_qucs_s.couplers import synthesize_coupler
from mcp_qucs_s.microstrip import Substrate


@pytest.fixture
def fr4() -> Substrate:
    return Substrate(er=4.4, h_mm=0.254)


def test_branch_line_has_four_quarter_wave_arms(fr4) -> None:
    design = synthesize_coupler("branch_line", 3.0, 1e9, 50.0, fr4)
    assert design.kind == "branch_line"
    assert design.coupling_db == 3.0
    # Four sections: 2 series, 2 shunt
    assert len(design.sections) == 4
    series_arms = [s for s in design.sections if "series" in s["role"]]
    shunt_arms = [s for s in design.sections if "shunt" in s["role"]]
    assert len(series_arms) == 2
    assert len(shunt_arms) == 2
    # Series-arm impedance is Z0/sqrt(2) ≈ 35.4 Ω
    assert series_arms[0]["z0"] == pytest.approx(50.0 / math.sqrt(2), rel=0.01)
    # Shunt-arm impedance equals Z0
    assert shunt_arms[0]["z0"] == pytest.approx(50.0, rel=0.01)


def test_branch_line_at_other_db_emits_warning(fr4) -> None:
    design = synthesize_coupler("branch_line", 6.0, 1e9, 50.0, fr4)
    assert any("3 dB" in n for n in design.notes)


def test_rat_race_uses_z0_sqrt2_ring(fr4) -> None:
    design = synthesize_coupler("rat_race", 3.0, 1e9, 50.0, fr4)
    assert design.kind == "rat_race"
    seg = design.sections[0]
    assert seg["z0"] == pytest.approx(50.0 * math.sqrt(2), rel=0.01)
    assert seg["count"] == 6


def test_coupled_line_10db_impedances(fr4) -> None:
    """For 10 dB coupling: C ≈ 0.316; Ze ≈ 69.4 Ω; Zo ≈ 36.0 Ω."""
    design = synthesize_coupler("coupled_line", 10.0, 1e9, 50.0, fr4)
    even = next(s for s in design.sections if s["role"] == "even_mode")
    odd = next(s for s in design.sections if s["role"] == "odd_mode")
    assert 65 < even["z0"] < 75
    assert 32 < odd["z0"] < 40
    # Geometric mean equals Z0
    assert math.sqrt(even["z0"] * odd["z0"]) == pytest.approx(50.0, rel=0.01)


def test_lange_emits_4_finger_note(fr4) -> None:
    design = synthesize_coupler("lange", 3.0, 1e9, 50.0, fr4)
    assert design.kind == "lange"
    assert any("Lange" in n or "interdigitated" in n for n in design.notes)


def test_unknown_kind_raises(fr4) -> None:
    with pytest.raises(ValueError):
        synthesize_coupler("magic_coupler", 3.0, 1e9, 50.0, fr4)  # type: ignore[arg-type]


def test_invalid_coupling_db_rejected(fr4) -> None:
    with pytest.raises(ValueError):
        synthesize_coupler("branch_line", -3.0, 1e9, 50.0, fr4)
    with pytest.raises(ValueError):
        synthesize_coupler("branch_line", 0.0, 1e9, 50.0, fr4)
