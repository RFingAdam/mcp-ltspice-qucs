"""Tests for the power-supply design tools."""

from __future__ import annotations

import math

import pytest

from mcp_ltspice.power import (
    analyze_ldo,
    compute_phase_margin,
    design_boost,
    design_buck,
    type2_compensator,
)
from mcp_ltspice.power.ldo import required_psrr_for_ripple_target

# ---- LDO -----------------------------------------------------------------


def test_ldo_basic_efficiency() -> None:
    """3.3V from 5V LDO at 100mA: efficiency = 66%, dissipation = 170mW."""
    res = analyze_ldo(v_in_v=5.0, v_out_v=3.3, i_out_a=0.1)
    assert res.headroom_v == pytest.approx(1.7)
    assert res.efficiency_pct == pytest.approx(66.0, abs=0.5)
    assert res.dissipation_w == pytest.approx(0.17)
    assert res.notes == [] or all("WARNING" not in n for n in res.notes)


def test_ldo_dropout_warning() -> None:
    """V_in - V_out below dropout fires a warning."""
    res = analyze_ldo(v_in_v=3.4, v_out_v=3.3, i_out_a=0.1, dropout_v=0.3)
    assert any("WARNING" in n for n in res.notes)


def test_ldo_high_dissipation_warning() -> None:
    """5V → 1.2V at 1A: dissipation = 3.8W → heatsink note."""
    res = analyze_ldo(v_in_v=5.0, v_out_v=1.2, i_out_a=1.0)
    assert any("Dissipation" in n or "heatsink" in n for n in res.notes)


def test_ldo_output_ripple_from_psrr() -> None:
    """50 mV ripple in, 60 dB PSRR → 50 µV ripple out."""
    res = analyze_ldo(
        v_in_v=5.0,
        v_out_v=3.3,
        i_out_a=0.1,
        psrr_db=60.0,
        v_ripple_in_mvpp=50.0,
    )
    # 50 mV / 1000 = 50 µV (since 60dB = 1000x)
    assert res.output_ripple_uvpp == pytest.approx(50.0, rel=0.01)


def test_required_psrr_helper() -> None:
    """50 mV → 100 µV requires 54 dB PSRR."""
    psrr = required_psrr_for_ripple_target(
        v_ripple_in_mvpp=50.0,
        v_ripple_out_uvpp_max=100.0,
    )
    assert psrr == pytest.approx(20 * math.log10(50000 / 100), rel=1e-6)


# ---- Buck ---------------------------------------------------------------


def test_buck_basic_design() -> None:
    """5V → 3.3V at 1A buck: D=66%, sane L and Cout."""
    d = design_buck(v_in_v=5.0, v_out_v=3.3, i_out_a=1.0, f_sw_hz=1e6)
    assert d.duty_cycle == pytest.approx(0.66, abs=0.01)
    # L should be in the µH range
    assert 1e-6 < d.L_h < 100e-6
    # Cout in µF range
    assert 1e-6 < d.Cout_f < 1000e-6
    assert d.inductor_peak_a > d.i_out_a  # peak > average


def test_buck_invalid_step_up_raises() -> None:
    with pytest.raises(ValueError, match="V_out < V_in"):
        design_buck(v_in_v=3.0, v_out_v=5.0, i_out_a=1.0)


def test_buck_low_duty_warning() -> None:
    """48V → 1.2V is 2.5% duty → warning."""
    d = design_buck(v_in_v=48.0, v_out_v=1.2, i_out_a=1.0)
    assert any("Duty cycle" in n and "low" in n for n in d.notes)


def test_buck_high_duty_warning() -> None:
    """5V → 4.5V is 90% duty → warning."""
    d = design_buck(v_in_v=5.0, v_out_v=4.5, i_out_a=1.0)
    assert any("Duty cycle" in n and "high" in n for n in d.notes)


# ---- Boost --------------------------------------------------------------


def test_boost_basic_design() -> None:
    """3.3V → 5V at 0.5A boost."""
    d = design_boost(v_in_v=3.3, v_out_v=5.0, i_out_a=0.5)
    expected_d = 1 - 3.3 / 5.0
    assert d.duty_cycle == pytest.approx(expected_d, rel=1e-6)
    assert d.L_h > 0 and d.Cout_f > 0


def test_boost_invalid_step_down_raises() -> None:
    with pytest.raises(ValueError, match="V_out > V_in"):
        design_boost(v_in_v=5.0, v_out_v=3.3, i_out_a=0.5)


def test_boost_high_step_up_warning() -> None:
    """3.3V → 24V is 86% duty → warning."""
    d = design_boost(v_in_v=3.3, v_out_v=24.0, i_out_a=0.1)
    assert any("Duty cycle" in n and "high" in n for n in d.notes)


# ---- Compensator + phase margin -----------------------------------------


def test_type2_compensator_returns_components() -> None:
    comp = type2_compensator(
        crossover_hz=10e3,
        plant_zero_hz=5e3,
        plant_pole_hz=20e3,
        phase_boost_deg=60.0,
    )
    assert comp.topology == "type2"
    assert comp.components["R_fb"] > 0
    assert comp.components["C_z"] > 0
    assert comp.components["C_p"] > 0


def test_type2_invalid_phase_boost_raises() -> None:
    with pytest.raises(ValueError):
        type2_compensator(
            crossover_hz=10e3,
            plant_zero_hz=5e3,
            plant_pole_hz=20e3,
            phase_boost_deg=120.0,
        )


def test_compute_phase_margin_finds_crossover() -> None:
    """Synthetic Bode: |H| = 100/(s+1) integrator. Crosses 0dB at ω≈100,
    phase asymptote = -90° → phase margin ≈ 90°."""
    import numpy as np

    f = np.geomspace(0.1, 10000, 200).tolist()
    # H(jω) = 100 / (1 + jω): |H| = 100/√(1+ω²), phase = -atan(ω)
    mag = [20 * math.log10(100 / math.sqrt(1 + (2 * math.pi * fi) ** 2)) for fi in f]
    phase = [-math.degrees(math.atan(2 * math.pi * fi)) for fi in f]
    res = compute_phase_margin(f, mag, phase)
    assert res["stable"] is True
    # At high ω the phase asymptotes to -90°; PM = 180-90 = 90°
    assert res["phase_margin_deg"] == pytest.approx(90.0, abs=2.0)


def test_compute_phase_margin_unstable() -> None:
    """If phase reaches -180° before |H|=0 dB, system is unstable."""
    f = [1, 10, 100, 1000]
    mag = [40, 30, 20, 10]  # never crosses 0 dB
    phase = [-180.0, -180.0, -180.0, -180.0]
    res = compute_phase_margin(f, mag, phase)
    assert res["stable"] is False
