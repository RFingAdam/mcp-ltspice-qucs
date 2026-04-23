"""Tests for the digital + mixed-signal analysis tools."""

from __future__ import annotations

import pytest

from mcp_ltspice.digital import (
    DigitalAggressor,
    TimingPath,
    check_setup_hold,
    estimate_digital_to_analog_crosstalk,
    estimate_supply_noise_injection,
    propagation_delay,
)

# ---- Setup/Hold check ----------------------------------------------------


def test_setup_check_passes_with_margin() -> None:
    """100 MHz clock (10 ns), 5 ns total path → 5 ns slack."""
    path = TimingPath(
        name="test",
        clk_period_ns=10.0,
        t_clk_q_ns=1.0,
        t_comb_ns=2.0,
        t_setup_ns=0.5,
        t_hold_ns=0.2,
    )
    res = check_setup_hold(path)
    assert res.setup_status == "pass"
    assert res.hold_status == "pass"
    assert res.setup_slack_ns == pytest.approx(6.5, rel=0.01)
    assert res.max_safe_clock_mhz > 100


def test_setup_violation_caught() -> None:
    path = TimingPath(
        name="too_fast",
        clk_period_ns=2.0,  # 500 MHz
        t_clk_q_ns=0.5,
        t_comb_ns=2.0,  # 2 ns of comb logic at 500 MHz!
        t_setup_ns=0.2,
        t_hold_ns=0.1,
    )
    res = check_setup_hold(path)
    assert res.setup_status == "fail"
    assert any("SETUP VIOLATION" in n for n in res.notes)


def test_hold_violation_with_excessive_skew() -> None:
    """Negative skew large enough to make hold slack negative."""
    path = TimingPath(
        name="skew",
        clk_period_ns=10.0,
        t_clk_q_ns=0.3,
        t_comb_ns=0.5,
        t_setup_ns=0.5,
        t_hold_ns=0.2,
        t_skew_ns=2.0,  # capture clock is 2 ns LATE → eats hold margin
    )
    res = check_setup_hold(path)
    # hold_slack = 0.3 + 0.5 - 0.2 - 2.0 = -1.4 ns
    assert res.hold_status == "fail"


def test_jitter_eats_setup_margin() -> None:
    """Adding jitter reduces setup slack."""
    base = TimingPath(
        name="jit",
        clk_period_ns=10.0,
        t_clk_q_ns=1.0,
        t_comb_ns=2.0,
        t_setup_ns=0.5,
        t_hold_ns=0.2,
    )
    jit = TimingPath(**{**base.__dict__, "t_jitter_ns": 1.0})
    res_base = check_setup_hold(base)
    res_jit = check_setup_hold(jit)
    assert res_jit.setup_slack_ns < res_base.setup_slack_ns
    assert res_jit.setup_slack_ns == pytest.approx(res_base.setup_slack_ns - 1.0)


# ---- Propagation delay ---------------------------------------------------


def test_propagation_delay_sums_gate_and_wire() -> None:
    """5 gates × 1 ns + 100 mm wire @ 5 ps/mm = 5.5 ns."""
    res = propagation_delay(
        n_gates=5,
        t_gate_avg_ns=1.0,
        wire_length_mm=100.0,
        t_wire_per_mm_ns=0.005,
    )
    assert res["total_delay_ns"] == pytest.approx(5.5)
    assert res["max_freq_mhz"] == pytest.approx(1000.0 / 5.5, rel=1e-6)


def test_propagation_delay_fanout_penalty() -> None:
    """Doubling fanout from 1 to 4 should add per-fanout penalty."""
    base = propagation_delay(
        n_gates=10,
        t_gate_avg_ns=1.0,
        wire_length_mm=0,
        fanout=1,
        t_per_fanout_ns=0.1,
    )
    fanned = propagation_delay(
        n_gates=10,
        t_gate_avg_ns=1.0,
        wire_length_mm=0,
        fanout=4,
        t_per_fanout_ns=0.1,
    )
    assert fanned["total_delay_ns"] > base["total_delay_ns"]
    assert fanned["fanout_penalty_ns"] == pytest.approx(3 * 0.1 * 10)


# ---- Digital → analog crosstalk ------------------------------------------


def test_crosstalk_increases_with_dvdt() -> None:
    """Faster rise time → more induced noise."""
    fast = DigitalAggressor(
        name="fast_clk",
        swing_v=3.3,
        rise_time_ns=0.5,
        switching_freq_mhz=100,
        capacitance_load_pf=10,
    )
    slow = DigitalAggressor(
        name="slow_clk",
        swing_v=3.3,
        rise_time_ns=5.0,
        switching_freq_mhz=100,
        capacitance_load_pf=10,
    )
    res_fast = estimate_digital_to_analog_crosstalk(fast, 50.0, 1e6)
    res_slow = estimate_digital_to_analog_crosstalk(slow, 50.0, 1e6)
    assert res_fast["victim_noise_peak_uv"] > res_slow["victim_noise_peak_uv"]


def test_crosstalk_severity_classification() -> None:
    """Tiny coupling gives 'low' concern; massive coupling gives 'critical'."""
    aggressor = DigitalAggressor(
        name="x",
        swing_v=3.3,
        rise_time_ns=1.0,
        switching_freq_mhz=100,
        capacitance_load_pf=10,
    )
    # Tiny coupling cap + low-Z victim → low
    low = estimate_digital_to_analog_crosstalk(aggressor, 0.01, 50.0)
    assert low["concern_level"] == "low"
    # Big coupling cap + high impedance victim → critical
    critical = estimate_digital_to_analog_crosstalk(aggressor, 100.0, 1e6)
    assert critical["concern_level"] in ("high", "critical")


# ---- Supply noise injection ---------------------------------------------


def test_supply_noise_injection_basic() -> None:
    aggressor = DigitalAggressor(
        name="dsp",
        swing_v=1.8,
        rise_time_ns=0.5,
        switching_freq_mhz=200,
        capacitance_load_pf=20,
    )
    res = estimate_supply_noise_injection(aggressor, supply_inductance_nh=5)
    assert res["v_droop_total_mv"] > 0
    assert res["i_switch_peak_ma"] > 0


def test_supply_noise_simultaneous_switches_scale() -> None:
    """8 simultaneous switches → 8× the current → larger droop."""
    aggressor = DigitalAggressor(
        name="bus",
        swing_v=1.8,
        rise_time_ns=0.5,
        switching_freq_mhz=100,
        capacitance_load_pf=10,
    )
    one = estimate_supply_noise_injection(aggressor, n_simultaneous_switches=1)
    eight = estimate_supply_noise_injection(aggressor, n_simultaneous_switches=8)
    assert eight["v_droop_total_mv"] > 5 * one["v_droop_total_mv"]


def test_high_droop_fires_warning() -> None:
    """Big driver + high L → big droop, should warn."""
    aggressor = DigitalAggressor(
        name="big",
        swing_v=3.3,
        rise_time_ns=0.2,
        switching_freq_mhz=100,
        capacitance_load_pf=200,
    )
    res = estimate_supply_noise_injection(
        aggressor,
        supply_inductance_nh=20,
        n_simultaneous_switches=8,
    )
    assert res["v_droop_pct_of_swing"] > 5
    assert any("V_droop" in n for n in res["notes"])
