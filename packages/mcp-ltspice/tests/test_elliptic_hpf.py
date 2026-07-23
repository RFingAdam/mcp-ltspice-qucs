"""Elliptic high-pass synthesis (issue #26, HPF portion).

An elliptic HPF is the frequency-mirror of an elliptic LPF: the LPF→HPF map
``ω → ω_c²/ω`` inverts every element into its dual and moves each finite zero
from ``ω_z`` to ``ω_c²/ω_z``, which for a high-pass lands in the *lower*
stopband. So the checks are: an equiripple passband above ``ω_c`` that mirrors
the LPF, deep notches below ``ω_c`` at exactly the mirrored zeros, and the
math-consistency invariant that each reported zero equals ``1/(2π√(L·C))`` of
its trap.

The response is checked against the analytical ladder here; the companion
integration test in this file confirms it against real qucsator.
"""

from __future__ import annotations

import math
import shutil
import subprocess

import numpy as np
import pytest

from mcp_ltspice.extract import (
    components_dict_to_elements,
    ladder_sparams_from_components,
)
from mcp_ltspice.synthesis.lc_filter import synthesize_lc_hpf

FC = 1e9


def _elements(design):
    return components_dict_to_elements(
        design.components, topology=str(design.topology), kind="highpass"
    )


def _s21_db(elements, freqs):
    s = ladder_sparams_from_components(elements, np.asarray(freqs), z0=50.0)
    return 20.0 * np.log10(np.abs(s[:, 1, 0]))


@pytest.mark.parametrize("order", [5, 7, 9])
def test_elliptic_hpf_is_a_highpass(order: int) -> None:
    d = synthesize_lc_hpf("elliptic", order=order, cutoff_hz=FC, stopband_atten_db=40)
    s21 = _s21_db(_elements(d), [FC * 0.1, FC, FC * 10])
    assert s21[2] > -0.5, f"order {order}: not passing at 10·fc ({s21[2]:.2f} dB)"
    assert s21[0] < -20.0, f"order {order}: not rejecting at 0.1·fc ({s21[0]:.2f} dB)"


@pytest.mark.parametrize("order", [5, 7, 9])
def test_transmission_zeros_fall_below_cutoff(order: int) -> None:
    d = synthesize_lc_hpf("elliptic", order=order, cutoff_hz=FC, stopband_atten_db=40)
    assert d.transmission_zeros_hz, "an elliptic filter must have finite zeros"
    for z in d.transmission_zeros_hz:
        assert z < FC, f"HPF zero {z / 1e9:.3f} GHz should be in the lower stopband"


@pytest.mark.parametrize("order", [5, 7, 9])
def test_notches_are_deep_at_the_reported_zeros(order: int) -> None:
    d = synthesize_lc_hpf("elliptic", order=order, cutoff_hz=FC, stopband_atten_db=40)
    s21 = _s21_db(_elements(d), d.transmission_zeros_hz)
    for z, level in zip(d.transmission_zeros_hz, s21, strict=True):
        assert level < -40.0, f"zero at {z / 1e9:.3f} GHz only {level:.1f} dB deep"


@pytest.mark.parametrize("order", [5, 7, 9])
def test_reported_zeros_match_trap_resonance(order: int) -> None:
    """The v0.2.0 math-consistency invariant: TZ == 1/(2π√(L·C)) per trap."""
    d = synthesize_lc_hpf("elliptic", order=order, cutoff_hz=FC, stopband_atten_db=40)
    trap_zeros = []
    for elt, params in _elements(d):
        if elt == "shunt_lc_trap":
            trap_zeros.append(1.0 / (2.0 * math.pi * math.sqrt(params["L"] * params["C"])))
    assert sorted(trap_zeros) == pytest.approx(sorted(d.transmission_zeros_hz), rel=1e-9)


def test_hpf_is_the_mirror_of_the_lpf() -> None:
    """|S21|(f) of the HPF should equal |S21|(ω_c²/f) of the LPF."""
    from mcp_ltspice.synthesis.lc_filter import synthesize_lc_lpf

    lpf = synthesize_lc_lpf("elliptic", order=5, cutoff_hz=FC, stopband_atten_db=40)
    hpf = synthesize_lc_hpf("elliptic", order=5, cutoff_hz=FC, stopband_atten_db=40)

    lpf_el = components_dict_to_elements(lpf.components, topology=str(lpf.topology), kind="lowpass")
    hpf_el = _elements(hpf)

    f = np.logspace(np.log10(2e8), np.log10(5e9), 60)
    f_mirror = FC**2 / f
    lpf_at_mirror = _s21_db(lpf_el, f_mirror)
    hpf_at_f = _s21_db(hpf_el, f)
    assert np.allclose(hpf_at_f, lpf_at_mirror, atol=0.05)


def test_z0_scales_the_components() -> None:
    d50 = synthesize_lc_hpf("elliptic", order=5, cutoff_hz=FC, z0=50.0)
    d75 = synthesize_lc_hpf("elliptic", order=5, cutoff_hz=FC, z0=75.0)
    # A 75 Ω design is well matched only when simulated at 75 Ω.
    s = ladder_sparams_from_components(_elements(d75), np.array([FC * 10]), z0=75.0)
    assert 20.0 * np.log10(abs(s[0, 0, 0])) < -15.0
    assert d50.components != d75.components


# ---------------------------------------------------------------------------
# Against a real simulator (qucsator), independent of the analytical ladder.
# ---------------------------------------------------------------------------

HAS_QUCS = shutil.which("qucsator_rf") is not None or shutil.which("qucsator") is not None


@pytest.mark.skipif(not HAS_QUCS, reason="qucsator not installed")
@pytest.mark.qucs
@pytest.mark.integration
def test_elliptic_hpf_matches_qucsator(tmp_path) -> None:
    from mcp_qucs_s.netlist import generate_ladder_netlist
    from mcp_qucs_s.sparams import network_from_dat

    d = synthesize_lc_hpf("elliptic", order=5, cutoff_hz=FC, stopband_atten_db=40)
    els = _elements(d)
    net = generate_ladder_netlist(
        els, tmp_path / "ehpf.net", f_start_hz=1e8, f_stop_hz=1e10, points=400
    )
    dat = tmp_path / "ehpf.dat"
    exe = shutil.which("qucsator_rf") or shutil.which("qucsator")
    subprocess.run([exe, "-i", str(net), "-o", str(dat)], capture_output=True, timeout=120)
    assert dat.is_file()

    nw = network_from_dat(dat)
    qs21 = 20.0 * np.log10(np.abs(nw.s[:, 1, 0]))
    analytic = _s21_db(els, nw.f)
    assert np.max(np.abs(qs21 - analytic)) < 0.05, "qucsator disagrees with the analytical ladder"
