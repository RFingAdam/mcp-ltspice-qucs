"""Tests for microstrip synthesis and analysis."""

from __future__ import annotations

import pytest

from mcp_qucs_s.microstrip import (
    Substrate,
    analyze_microstrip,
    synthesize_microstrip_line,
    synthesize_width,
)


@pytest.fixture
def fr4() -> Substrate:
    """Standard FR-4 substrate, 0.254 mm height, 1 oz copper."""
    return Substrate(er=4.4, h_mm=0.254, t_um=35.0, tan_d=0.02)


@pytest.fixture
def rogers4350b() -> Substrate:
    """Rogers RO4350B, 0.508 mm height, 1 oz copper."""
    return Substrate(er=3.55, h_mm=0.508, t_um=35.0, tan_d=0.0037)


def test_50ohm_on_fr4(fr4) -> None:
    """50 Ω microstrip on 0.254 mm FR-4 needs ~0.46 mm width."""
    width = synthesize_width(50.0, fr4)
    assert 0.4 < width < 0.55


def test_50ohm_on_rogers(rogers4350b) -> None:
    """50 Ω microstrip on 0.508 mm RO4350B needs ~1.1 mm width."""
    width = synthesize_width(50.0, rogers4350b)
    assert 0.95 < width < 1.25


def test_synthesize_then_analyze_round_trip(fr4) -> None:
    """Synthesize a width for 50 Ω, then analyze and confirm 50 Ω back."""
    target = 50.0
    width = synthesize_width(target, fr4)
    result = analyze_microstrip(width, fr4, freq_hz=1e9)
    # Within 1% accuracy for the Hammerstad-Jensen synthesis equations
    assert result["z0_ohm"] == pytest.approx(target, rel=0.01)


def test_higher_z0_means_narrower_trace(fr4) -> None:
    w50 = synthesize_width(50.0, fr4)
    w75 = synthesize_width(75.0, fr4)
    w100 = synthesize_width(100.0, fr4)
    assert w50 > w75 > w100


def test_eff_permittivity_below_substrate_er(fr4) -> None:
    """Effective ε is always less than the substrate ε_r (because some
    field is in air)."""
    result = analyze_microstrip(0.5, fr4)
    assert result["er_eff"] < fr4.er
    assert result["er_eff"] > 1.0


def test_quarter_wave_at_915mhz_on_fr4(fr4) -> None:
    """A 50 Ω quarter-wave transformer at 915 MHz on FR-4."""
    line = synthesize_microstrip_line(50.0, 90.0, 915e6, fr4)
    # Length should be lambda_eff / 4 ≈ 49 mm
    assert 30 < line.length_mm < 65
    assert line.electrical_length_deg == 90.0
    assert line.metadata["z0_error_pct"] == pytest.approx(0.0, abs=1.0)


def test_invalid_substrate_raises(fr4) -> None:
    with pytest.raises(ValueError):
        synthesize_width(-50.0, fr4)
    with pytest.raises(ValueError):
        analyze_microstrip(-1.0, fr4)


def test_electrical_length_out_of_range_rejected(fr4) -> None:
    with pytest.raises(ValueError):
        synthesize_microstrip_line(50.0, -10.0, 1e9, fr4)
    with pytest.raises(ValueError):
        synthesize_microstrip_line(50.0, 1000.0, 1e9, fr4)


def test_thicker_substrate_means_wider_trace_for_same_z0() -> None:
    thin = Substrate(er=4.4, h_mm=0.1)
    thick = Substrate(er=4.4, h_mm=0.5)
    assert synthesize_width(50.0, thick) > synthesize_width(50.0, thin)
