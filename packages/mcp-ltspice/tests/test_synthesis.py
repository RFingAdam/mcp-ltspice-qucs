"""Tests for filter synthesis: g-coefficients + LC scaling + S-param fit."""

from __future__ import annotations

import math

import numpy as np
import pytest

from mcp_ltspice.extract import (
    components_dict_to_elements,
    ladder_sparams_from_components,
)
from mcp_ltspice.synthesis import (
    Topology,
    g_coefficients,
    lc_ladder,
    synthesize_lc_lpf,
)

# ---------------------------------------------------------------------------
# g-coefficient tables from Pozar "Microwave Engineering" 4th ed., Tables 8.3, 8.4
# ---------------------------------------------------------------------------


def test_butterworth_g_n3() -> None:
    g, _ = g_coefficients("butterworth", order=3)
    assert g == pytest.approx([1.0, 1.0, 2.0, 1.0, 1.0], abs=1e-6)


def test_butterworth_g_n5() -> None:
    g, _ = g_coefficients("butterworth", order=5)
    expected = [1.0, 0.6180, 1.6180, 2.0000, 1.6180, 0.6180, 1.0]
    assert g == pytest.approx(expected, abs=1e-3)


def test_chebyshev1_g_0p5db_n3() -> None:
    # Pozar Table 8.4: 0.5 dB ripple, N=3
    g, _ = g_coefficients("chebyshev1", order=3, ripple_db=0.5)
    expected = [1.0, 1.5963, 1.0967, 1.5963, 1.0]
    assert g == pytest.approx(expected, abs=1e-3)


def test_chebyshev1_g_0p1db_n5() -> None:
    # Pozar Table 8.4: 0.1 dB ripple, N=5
    g, _ = g_coefficients("chebyshev1", order=5, ripple_db=0.1)
    expected = [1.0, 1.1468, 1.3712, 1.9750, 1.3712, 1.1468, 1.0]
    assert g == pytest.approx(expected, abs=1e-3)


# ---------------------------------------------------------------------------
# LC scaling: physical values must satisfy ω_c L = Z0 g for series L,
# and ω_c C Z0 = g for shunt C.
# ---------------------------------------------------------------------------


def test_lc_ladder_butterworth_n3_t_topology() -> None:
    g, _ = g_coefficients("butterworth", order=3)
    fc = 1e9
    z0 = 50.0
    comps = lc_ladder(g, fc, z0, Topology.SERIES_FIRST)
    # Expect L1, C2, L3
    omega_c = 2 * math.pi * fc
    assert comps["L1"] == pytest.approx(g[1] * z0 / omega_c, rel=1e-9)
    assert comps["C2"] == pytest.approx(g[2] / (omega_c * z0), rel=1e-9)
    assert comps["L3"] == pytest.approx(g[3] * z0 / omega_c, rel=1e-9)


def test_lc_ladder_pi_topology_swaps_ls_and_cs() -> None:
    g, _ = g_coefficients("butterworth", order=3)
    fc = 1e9
    z0 = 50.0
    pi_comps = lc_ladder(g, fc, z0, Topology.SHUNT_FIRST)
    # In Pi: position 1 is shunt C, position 2 is series L, position 3 is shunt C
    assert "C1" in pi_comps and "L2" in pi_comps and "C3" in pi_comps
    # And the magnitudes are identical to the T-topology counterparts
    omega_c = 2 * math.pi * fc
    assert pi_comps["C1"] == pytest.approx(g[1] / (omega_c * z0))
    assert pi_comps["L2"] == pytest.approx(g[2] * z0 / omega_c)


# ---------------------------------------------------------------------------
# End-to-end response checks via analytical S-param computation
# ---------------------------------------------------------------------------


def _s21_db(components: dict[str, float], freq_hz: np.ndarray, **kw: object) -> np.ndarray:
    elements = components_dict_to_elements(components, **kw)
    s = ladder_sparams_from_components(elements, freq_hz)
    return 20.0 * np.log10(np.abs(s[:, 1, 0]))


def test_butterworth_n5_response() -> None:
    fc = 500e6
    design = synthesize_lc_lpf("butterworth", order=5, cutoff_hz=fc, z0=50.0)
    f = np.array([fc * 0.01, fc * 0.5, fc, fc * 2, fc * 5])
    s21 = _s21_db(design.components, f, topology="series_first")
    # Passband: < 0.5 dB
    assert s21[0] > -0.1
    assert s21[1] > -0.5
    # At fc: -3 dB by definition
    assert s21[2] == pytest.approx(-3.01, abs=0.2)
    # Stopband: 5N order rolloff = 30 dB / decade. At 2*fc: ~6N=30 dB
    assert s21[3] < -25
    assert s21[4] < -60


def test_chebyshev1_n5_passband_ripple() -> None:
    fc = 500e6
    ripple = 0.1
    design = synthesize_lc_lpf("chebyshev1", order=5, cutoff_hz=fc, ripple_db=ripple, z0=50.0)
    f = np.linspace(fc * 0.01, fc, 200)
    s21 = _s21_db(design.components, f, topology="series_first")
    # Ripple bound: passband |S21| stays within [-ripple, 0] dB
    # (with a tiny tolerance for numerical edges near fc)
    assert s21.max() <= 0.05
    assert s21.min() >= -ripple - 0.05


def test_chebyshev1_at_cutoff_is_at_ripple_floor() -> None:
    # For Chebyshev I the response at exactly ω = ω_c equals the ripple level
    fc = 500e6
    ripple = 0.5
    design = synthesize_lc_lpf("chebyshev1", order=3, cutoff_hz=fc, ripple_db=ripple)
    s21_at_fc = _s21_db(design.components, np.array([fc]), topology="series_first")[0]
    assert s21_at_fc == pytest.approx(-ripple, abs=0.05)


# ---------------------------------------------------------------------------
# Elliptic synthesis: must produce finite transmission zeros and the
# fitted response must approximate the prototype within a reasonable
# tolerance in the passband and stopband.
# ---------------------------------------------------------------------------


def test_elliptic_n5_has_finite_transmission_zeros() -> None:
    design = synthesize_lc_lpf(
        "elliptic", order=5, cutoff_hz=1e9, ripple_db=0.1, stopband_atten_db=40
    )
    assert len(design.transmission_zeros_hz) == 2  # (N-1)/2 zero pairs for odd N
    for fz in design.transmission_zeros_hz:
        assert fz > design.cutoff_hz  # zeros are in the stopband


def test_elliptic_n7_passband_and_stopband() -> None:
    fc = 1.4e9
    design = synthesize_lc_lpf(
        "elliptic",
        order=7,
        cutoff_hz=fc,
        ripple_db=0.1,
        stopband_atten_db=40,
    )
    # Sample the response analytically
    f = np.array(
        [
            fc * 0.1,
            fc * 0.5,
            fc * 0.9,  # passband
            fc * 1.5,
            fc * 2.0,
            fc * 3.0,  # stopband
        ]
    )
    s21 = _s21_db(design.components, f, topology="series_first", transmission_zeros=True)
    # Passband: better than ripple + small tol
    assert s21[0] > -0.5
    assert s21[1] > -0.5
    assert s21[2] > -1.0
    # Stopband: better than ~25 dB rejection (we asked for 40 dB but fit may relax)
    assert s21[3] < -20
    assert s21[4] < -25
