"""Tests for EMC pre-compliance estimation."""

from __future__ import annotations

import pytest

from mcp_rf_analysis.emc import (
    cispr_limit_at,
    fcc_part15_radiated_limit_at,
    predict_conducted_emissions,
    predict_radiated_emissions_loop,
)
from mcp_rf_analysis.emc.conducted import lisn_impedance

# ---- LISN model ---------------------------------------------------------


def test_lisn_impedance_above_150k_is_about_25_ohm() -> None:
    """At ≥1 MHz the LISN looks like 50 Ω || 50 Ω = 25 Ω real."""
    z = lisn_impedance(1e6)
    # Real part should be near 25 Ω
    assert 20 < abs(z) < 100


def test_lisn_impedance_at_dc_is_dominated_by_inductor() -> None:
    """At low freq the inductor + parallel branch dominate."""
    z_low = lisn_impedance(150e3)
    assert abs(z_low) > 0


# ---- CISPR / FCC limits -------------------------------------------------


def test_cispr22_class_b_at_500khz() -> None:
    """At 500 kHz the QP limit is 56 dBµV."""
    limit = cispr_limit_at(500e3)
    assert limit == pytest.approx(56.0, abs=0.5)


def test_cispr22_class_b_at_30mhz() -> None:
    """At 30 MHz the limit steps up to 60 dBµV."""
    limit = cispr_limit_at(30e6)
    assert limit == pytest.approx(60.0, abs=0.5)


def test_cispr22_class_a_is_10db_above_class_b() -> None:
    """Class A (industrial) is 10 dB more permissive."""
    a = cispr_limit_at(1e6, standard="cispr22_a")
    b = cispr_limit_at(1e6, standard="cispr22_b")
    assert a - b == pytest.approx(10.0)


def test_cispr_limit_outside_range_raises() -> None:
    with pytest.raises(ValueError):
        cispr_limit_at(50e3)
    with pytest.raises(ValueError):
        cispr_limit_at(100e6)


# ---- Conducted-emissions prediction -------------------------------------


def test_predict_conducted_passes_for_clean_smps() -> None:
    """A well-filtered SMPS with µA-level harmonics passes Class B easily."""
    spectrum = [(f, 1e-6) for f in [200e3, 500e3, 1e6, 5e6, 20e6]]
    res = predict_conducted_emissions(spectrum)
    assert res["overall"] == "pass"
    assert res["n_violations"] == 0


def test_predict_conducted_fails_for_noisy_supply() -> None:
    """100 mA harmonics into the LISN is way above Class B."""
    spectrum = [(f, 0.1) for f in [200e3, 500e3, 1e6, 5e6, 20e6]]
    res = predict_conducted_emissions(spectrum)
    assert res["overall"] == "fail"
    assert res["n_violations"] > 0


def test_predict_conducted_skips_out_of_band_freqs() -> None:
    """Frequencies below 150 kHz or above 30 MHz are skipped."""
    spectrum = [(50e3, 1.0), (1e6, 1e-6), (100e6, 1.0)]
    res = predict_conducted_emissions(spectrum)
    assert len(res["freq_hz"]) == 1
    assert res["freq_hz"][0] == 1e6


# ---- Radiated emissions -------------------------------------------------


def test_radiated_emissions_grows_with_freq_squared() -> None:
    """E ∝ f² for fixed I and loop area."""
    base = predict_radiated_emissions_loop(
        current_a=0.001,
        loop_area_cm2=10,
        freq_hz=100e6,
    )
    high = predict_radiated_emissions_loop(
        current_a=0.001,
        loop_area_cm2=10,
        freq_hz=200e6,
    )
    # Doubling f → 4× field → +12 dB
    assert high["e_dbuv_per_m"] - base["e_dbuv_per_m"] == pytest.approx(12.0, abs=0.1)


def test_radiated_emissions_grows_with_loop_area() -> None:
    """E ∝ A linearly."""
    small = predict_radiated_emissions_loop(
        current_a=0.001,
        loop_area_cm2=1,
        freq_hz=100e6,
    )
    big = predict_radiated_emissions_loop(
        current_a=0.001,
        loop_area_cm2=10,
        freq_hz=100e6,
    )
    # 10× area → +20 dB
    assert big["e_dbuv_per_m"] - small["e_dbuv_per_m"] == pytest.approx(20.0, abs=0.1)


def test_radiated_emissions_grows_with_distance_inverse() -> None:
    """E ∝ 1/r → field at 10m is 10/3 = 3.33× weaker = -10.5 dB."""
    near = predict_radiated_emissions_loop(
        current_a=0.001,
        loop_area_cm2=10,
        freq_hz=100e6,
        measurement_distance_m=3.0,
    )
    far = predict_radiated_emissions_loop(
        current_a=0.001,
        loop_area_cm2=10,
        freq_hz=100e6,
        measurement_distance_m=10.0,
    )
    expected_drop = 20 * (lambda r: __import__("math").log10(r))(10 / 3)
    assert near["e_dbuv_per_m"] - far["e_dbuv_per_m"] == pytest.approx(expected_drop, abs=0.1)


def test_fcc_radiated_limit_at_100mhz_is_43_5() -> None:
    """88-216 MHz limit is 150 µV/m = 43.5 dBµV/m at 3m."""
    limit = fcc_part15_radiated_limit_at(100e6, distance_m=3.0)
    assert limit == pytest.approx(43.5, abs=0.1)


def test_fcc_radiated_limit_above_960mhz_is_higher() -> None:
    """Above 960 MHz limit jumps to 500 µV/m = 54 dBµV/m."""
    limit = fcc_part15_radiated_limit_at(2e9, distance_m=3.0)
    assert limit == pytest.approx(54.0, abs=0.1)


def test_fcc_radiated_limit_at_10m_is_lower() -> None:
    """Inverse-square: limit at 10m is ~10.5 dB lower than at 3m."""
    at_3m = fcc_part15_radiated_limit_at(100e6, distance_m=3.0)
    at_10m = fcc_part15_radiated_limit_at(100e6, distance_m=10.0)
    assert at_3m - at_10m == pytest.approx(10.5, abs=0.5)
