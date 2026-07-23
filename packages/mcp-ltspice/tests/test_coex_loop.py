"""synthesize_for_coex_target (issue #14): the closed loop.

One call wraps the whole workflow the engineer used to orchestrate by
hand: place zeros for the coex victims (#12) → synthesize the elliptic
LPF and aim its traps → substitute real vendor parts (SRF-checked) →
evaluate the realized filter's rejection analytically → run the coex
matrix (#15, GNSS-aware) → escalate the order until the worst-case
desense meets the target or max_order is reached.

The tests drive the real pipeline end to end (real synthesis fits, real
vendor tables, real matrix) — no mocks; runtime is dominated by the
elliptic LSQ fit at ~50 ms per order.
"""

from __future__ import annotations

import pytest

from mcp_ltspice.coex_loop import synthesize_for_coex_target

PASSBAND = (902e6, 928e6)
LTE_B3 = {
    "name": "LTE B3 DL",
    "f_center_hz": 1842.5e6,
    "bandwidth_hz": 75e6,
    "sensitivity_dbm": -97.0,
}
GPS_L1 = {
    "name": "GPS L1",
    "f_center_hz": 1.57542e9,
    "bandwidth_hz": 2.046e6,
    "victim_type": "gnss",
    "sensitivity_dbm": -160.0,
    "noise_figure_db": 2.0,
}


def test_easy_target_converges_at_min_order() -> None:
    result = synthesize_for_coex_target(
        PASSBAND,
        pa_power_dbm=20.0,
        victim_bands=[LTE_B3],
        target_max_desense_db=6.0,
        antenna_iso_db=30.0,
        min_order=5,
        max_order=9,
    )
    assert result["converged"] is True
    assert result["chosen_order"] == 5
    assert len(result["iterations"]) == 1
    assert result["iterations"][0]["worst_desense_db"] <= 6.0
    assert result["components"], "realized component values must be returned"


def test_hard_target_escalates_order() -> None:
    """A brutal target at high PA power forces at least one escalation;
    every iteration is logged with its worst-case numbers."""
    result = synthesize_for_coex_target(
        PASSBAND,
        pa_power_dbm=36.0,
        victim_bands=[LTE_B3],
        target_max_desense_db=0.0,
        antenna_iso_db=15.0,
        min_order=5,
        max_order=9,
    )
    its = result["iterations"]
    assert len(its) >= 2, "0 dB desense at 36 dBm through 15 dB iso should not pass at order 5"
    assert [i["order"] for i in its] == list(range(5, 5 + 2 * len(its), 2))
    assert all("worst_desense_db" in i and "worst_entry" in i for i in its)
    if not result["converged"]:
        assert result["chosen_order"] == its[-1]["order"], "best-so-far must be returned"


def test_monotone_improvement_with_order() -> None:
    """Higher order should not make the worst-case desense worse."""
    result = synthesize_for_coex_target(
        PASSBAND,
        pa_power_dbm=36.0,
        victim_bands=[LTE_B3],
        target_max_desense_db=-100.0,  # unattainable: forces the full sweep
        antenna_iso_db=15.0,
        min_order=5,
        max_order=9,
    )
    assert result["converged"] is False
    worst = [i["worst_desense_db"] for i in result["iterations"]]
    assert len(worst) == 3
    assert worst[-1] <= worst[0] + 0.5, f"order sweep should not degrade: {worst}"


def test_gnss_victim_flows_through_with_cn0_metric() -> None:
    result = synthesize_for_coex_target(
        PASSBAND,
        pa_power_dbm=30.0,
        victim_bands=[GPS_L1],
        target_max_desense_db=6.0,
        antenna_iso_db=25.0,
        min_order=5,
        max_order=7,
    )
    gnss_rows = [r for r in result["coex_matrix"] if r["victim"] == "GPS L1"]
    assert gnss_rows, "GNSS victim must appear in the final matrix"
    assert all("delta_cn0_db_hz" in r for r in gnss_rows)
    bb = [r for r in gnss_rows if r["mechanism"] == "broadband_noise"]
    assert bb and bb[0]["assumptions"]["filter_rejection_at_victim_db"] > 20.0, (
        "the realized filter's rejection at L1 must be injected into the GNSS model"
    )


def test_zeros_plan_is_reported_with_the_filter() -> None:
    result = synthesize_for_coex_target(
        PASSBAND,
        pa_power_dbm=20.0,
        victim_bands=[LTE_B3],
        target_max_desense_db=6.0,
        antenna_iso_db=30.0,
        min_order=5,
        max_order=5,
    )
    plan = result["zeros_plan"]
    assert plan["zeros"], "the coex zero plan must be part of the result"
    z2h = next(z for z in plan["zeros"] if z["harmonic"] == 2)
    assert 1804e6 < z2h["target_freq_hz"] < 1880e6


def test_invalid_inputs_rejected() -> None:
    with pytest.raises(ValueError, match="min_order"):
        synthesize_for_coex_target(
            PASSBAND, pa_power_dbm=20.0, victim_bands=[LTE_B3], min_order=9, max_order=5
        )
    with pytest.raises(ValueError, match="passband"):
        synthesize_for_coex_target((928e6, 902e6), pa_power_dbm=20.0, victim_bands=[LTE_B3])


def test_tool_envelope() -> None:
    from mcp_ltspice import server

    env = server.synthesize_for_coex_target(
        passband_hz=[902e6, 928e6],
        pa_power_dbm=20.0,
        victim_bands=[LTE_B3],
        target_max_desense_db=6.0,
        antenna_iso_db=30.0,
        min_order=5,
        max_order=7,
    )
    assert env.status == "ok"
    assert env.data["converged"] is True


def test_tool_error_envelope() -> None:
    from mcp_ltspice import server

    env = server.synthesize_for_coex_target(
        passband_hz=[928e6, 902e6],
        pa_power_dbm=20.0,
        victim_bands=[LTE_B3],
    )
    assert env.status == "error"
    assert "passband" in env.error
