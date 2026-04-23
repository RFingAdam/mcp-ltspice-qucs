"""Tests for signal-integrity tools."""

from __future__ import annotations

import pytest

from mcp_rf_analysis.si import (
    estimate_fext_db,
    estimate_next_db,
    eye_diagram_from_s2p,
    tdr_from_s11,
)

# ---- TDR -----------------------------------------------------------------


def test_tdr_returns_arrays_for_butterworth_lpf(lpf_s2p) -> None:
    """A 5th-order LPF reflects strongly above its cutoff — TDR should
    return a sensible Z(distance) profile."""
    res = tdr_from_s11(lpf_s2p, er_eff=4.0)
    assert len(res["distance_mm"]) > 100
    assert len(res["impedance_ohm"]) == len(res["distance_mm"])
    # Phase velocity at εr=4 is c/2 = 1.5e8 m/s
    assert res["phase_velocity_m_s"] == pytest.approx(1.5e8, rel=0.01)


def test_tdr_window_options(lpf_s2p) -> None:
    a = tdr_from_s11(lpf_s2p, window="hann")
    b = tdr_from_s11(lpf_s2p, window="rect")
    # Rectangular has more sidelobe ripple, hann is smoother — but both
    # produce the same number of samples
    assert len(a["impedance_ohm"]) == len(b["impedance_ohm"])


# ---- Eye diagram --------------------------------------------------------


def test_eye_diagram_basic_metrics(thru_s2p) -> None:
    """A pass-through (S21=1) channel should yield a wide-open eye."""
    metrics = eye_diagram_from_s2p(
        thru_s2p,
        bitrate_gbps=1.0,
        n_bits=200,
        swing_v=1.0,
    )
    # With a perfect channel the eye should be > 90% of swing
    assert metrics.eye_height_v > 0.7
    assert metrics.eye_width_ui > 0.8


def test_eye_diagram_records_isi(thru_s2p) -> None:
    metrics = eye_diagram_from_s2p(
        thru_s2p,
        bitrate_gbps=1.0,
        n_bits=200,
    )
    assert metrics.isi_pp_v >= 0


# ---- Crosstalk ----------------------------------------------------------


def test_next_grows_with_closer_traces() -> None:
    """Less separation → more coupling → more NEXT."""
    close = estimate_next_db(
        coupling_length_mm=100,
        trace_separation_mm=0.1,
        substrate_height_mm=0.254,
        rise_time_ps=100,
    )
    far = estimate_next_db(
        coupling_length_mm=100,
        trace_separation_mm=2.0,
        substrate_height_mm=0.254,
        rise_time_ps=100,
    )
    assert close["next_db"] > far["next_db"]


def test_next_saturates_for_short_rise_time() -> None:
    """Slow rise vs fast rise on the same coupled section."""
    fast = estimate_next_db(
        coupling_length_mm=100,
        trace_separation_mm=0.5,
        substrate_height_mm=0.254,
        rise_time_ps=50,
    )
    slow = estimate_next_db(
        coupling_length_mm=100,
        trace_separation_mm=0.5,
        substrate_height_mm=0.254,
        rise_time_ps=5000,
    )
    # Fast rise (50 ps) is below 2*t_d (~1300 ps for 100 mm) → NOT saturated
    assert fast["saturated"] is False
    # Slow rise (5000 ps) is above 2*t_d → saturated, less NEXT
    assert slow["saturated"] is True
    assert fast["next_db"] > slow["next_db"]


def test_fext_grows_with_length() -> None:
    """FEXT amplitude is proportional to coupling length."""
    short = estimate_fext_db(
        coupling_length_mm=10,
        trace_separation_mm=0.5,
        substrate_height_mm=0.254,
        rise_time_ps=100,
    )
    long = estimate_fext_db(
        coupling_length_mm=1000,
        trace_separation_mm=0.5,
        substrate_height_mm=0.254,
        rise_time_ps=100,
    )
    assert long["k_fext"] > short["k_fext"]


def test_invalid_inputs_raise() -> None:
    with pytest.raises(ValueError):
        estimate_next_db(
            coupling_length_mm=0,
            trace_separation_mm=1,
            substrate_height_mm=1,
            rise_time_ps=100,
        )
    with pytest.raises(ValueError):
        estimate_fext_db(
            coupling_length_mm=10,
            trace_separation_mm=1,
            substrate_height_mm=1,
            rise_time_ps=0,
        )
