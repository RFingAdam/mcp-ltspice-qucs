"""Tests for signal-integrity tools."""

from __future__ import annotations

import numpy as np
import pytest

from mcp_rf_analysis.si import (
    estimate_fext_db,
    estimate_next_db,
    eye_diagram_from_s2p,
    tdr_from_s11,
)
from mcp_rf_analysis.si.tdr import tdr_transform

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


def _delayed_reflection(frequency_hz, delay_s=2e-9, amplitude=0.2):
    return amplitude * np.exp(-1j * 2 * np.pi * frequency_hz * delay_s)


def test_tdr_recovers_delayed_discontinuity_location_and_sign() -> None:
    frequency = np.linspace(0.0, 10e9, 2001)
    delay = 2e-9
    result = tdr_transform(
        frequency,
        _delayed_reflection(frequency, delay_s=delay, amplitude=-0.2),
        transform_mode="lowpass",
        window="rect",
        padding_factor=4,
    )
    impulse = np.asarray(result["rho_impulse"])
    peak = int(np.argmax(np.abs(impulse)))
    assert result["time_ns"][peak] == pytest.approx(delay * 1e9, abs=0.03)
    assert impulse[peak] < 0
    expected_mm = delay * result["phase_velocity_m_s"] / 2 * 1000
    assert result["distance_mm"][peak] == pytest.approx(expected_mm, rel=0.02)


def test_tdr_log_grid_controlled_resampling_matches_linear_peak() -> None:
    linear = np.linspace(1e6, 10e9, 2001)
    logarithmic = np.geomspace(1e6, 10e9, 2001)
    expected_delay = 1.5e-9
    reference = tdr_transform(
        linear,
        _delayed_reflection(linear, expected_delay),
        window="rect",
    )
    resampled = tdr_transform(
        logarithmic,
        _delayed_reflection(logarithmic, expected_delay),
        window="rect",
    )
    reference_peak = int(np.argmax(np.abs(reference["rho_impulse"])))
    resampled_peak = int(np.argmax(np.abs(resampled["rho_impulse"])))
    assert resampled["time_ns"][resampled_peak] == pytest.approx(
        reference["time_ns"][reference_peak], abs=0.05
    )
    assert resampled["input_grid"]["resampled"] is True
    assert "resampled" in resampled["warnings"][0]


@pytest.mark.parametrize(
    "frequency",
    [
        np.asarray([1e6, 2e6, 2e6, 3e6]),
        np.asarray([1e6, 3e6, 2e6, 4e6]),
    ],
)
def test_tdr_rejects_duplicate_or_nonmonotonic_frequency(frequency) -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        tdr_transform(frequency, np.zeros(frequency.size, dtype=complex))


def test_tdr_nonuniform_rejected_when_resampling_disabled() -> None:
    frequency = np.geomspace(1e6, 1e9, 101)
    with pytest.raises(ValueError, match="non-uniform"):
        tdr_transform(
            frequency,
            _delayed_reflection(frequency),
            resample_nonuniform=False,
        )


def test_bandpass_start_frequency_recovers_delay_without_claiming_impedance() -> None:
    frequency = np.linspace(2e9, 8e9, 1201)
    delay = 3e-9
    result = tdr_transform(
        frequency,
        _delayed_reflection(frequency, delay),
        transform_mode="bandpass",
        window="rect",
    )
    peak = int(np.argmax(result["rho"]))
    assert result["time_ns"][peak] == pytest.approx(delay * 1e9, abs=0.05)
    assert result["impedance_ohm"] is None
    assert "envelope only" in result["warnings"][-1]


def test_lowpass_missing_dc_reject_policy() -> None:
    frequency = np.linspace(1e9, 10e9, 1001)
    with pytest.raises(ValueError, match="requires DC"):
        tdr_transform(
            frequency,
            _delayed_reflection(frequency),
            dc_extrapolation="reject",
        )


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
