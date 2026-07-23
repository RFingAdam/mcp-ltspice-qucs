"""Elliptic band-pass / band-stop synthesis (issue #26, BPF/BSF portion).

The elliptic LPF prototype is series-L sections interleaved with shunt
series-LC traps. The BPF map ``ω → (1/Δ)(ω/ω₀ − ω₀/ω)`` and the BSF map
``ω → Δ/(ω/ω₀ − ω₀/ω)`` transform each L and C *inside* the trap
separately (inductor → series-LC for BPF / parallel-LC for BSF, capacitor
→ the dual), so every LPF trap becomes a FOUR-element shunt branch: a
series-LC in series with a parallel-LC tank, to ground. That branch shorts
at two frequencies — the two images of the LPF zero ``ω_z`` under the map,
the roots of ``r² ∓ 2b·r − 1 = 0`` in ``r = ω/ω₀`` with ``b = ω_z·Δ/2``
(BPF) or ``b = Δ/(2·ω_z)`` (BSF): ``ω = ω₀(√(b²+1) ± b)``, geometric
mirror-pairs about ω₀.

Because the map is exact algebra on the prototype (no fitting), the BPF /
BSF response must equal the already-validated elliptic LPF response
composed with the map — that exactness is asserted here, plus the v0.2.0
math-consistency invariant generalized to composite branches: the reported
``transmission_zeros_hz`` must be the roots of

    u²·L_sC_sL_pC_p − u·(L_sC_s + L_pC_p + L_pC_s) + 1 = 0,   u = (2πf)²

computed from the *physical* component values. The companion integration
tests confirm |S21| against real qucsator.
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
from mcp_ltspice.synthesis.lc_filter import (
    g_coefficients,
    synthesize_lc_bpf,
    synthesize_lc_bsf,
    synthesize_lc_lpf,
)

F_LOW = 900e6
F_HIGH = 1100e6
F0 = math.sqrt(F_LOW * F_HIGH)
DELTA = (F_HIGH - F_LOW) / F0
ATTEN = 40.0


def _elements(design):
    return components_dict_to_elements(
        design.components, topology=str(design.topology), kind=design.metadata["kind"]
    )


def _s21_db(elements, freqs):
    s = ladder_sparams_from_components(elements, np.asarray(freqs, dtype=float), z0=50.0)
    return 20.0 * np.log10(np.maximum(np.abs(s[:, 1, 0]), 1e-12))


def _composite_branch_zeros_hz(params: dict[str, float]) -> list[float]:
    """The two frequencies where a shunt composite trap branch shorts."""
    lscs = params["L_s"] * params["C_s"]
    lpcp = params["L_p"] * params["C_p"]
    lpcs = params["L_p"] * params["C_s"]
    # u²·L_sC_sL_pC_p − u·(L_sC_s + L_pC_p + L_pC_s) + 1 = 0, u = ω²
    roots = np.roots([lscs * lpcp, -(lscs + lpcp + lpcs), 1.0])
    return sorted(float(math.sqrt(u)) / (2.0 * math.pi) for u in roots.real)


def _mapped_zeros_hz(order: int, kind: str) -> list[float]:
    """Images of the LPF prototype zeros under the BPF / BSF map."""
    _, zeros_norm = g_coefficients("elliptic", order, 0.1, ATTEN)
    out = []
    for wz in zeros_norm:
        b = wz * DELTA / 2.0 if kind == "bandpass" else DELTA / (2.0 * wz)
        root = math.sqrt(b * b + 1.0)
        out.extend([F0 * (root - b), F0 * (root + b)])
    return sorted(out)


@pytest.fixture(scope="module", params=[5, 7, 9])
def bpf_design(request):
    return synthesize_lc_bpf(
        "elliptic", order=request.param, f_low_hz=F_LOW, f_high_hz=F_HIGH, stopband_atten_db=ATTEN
    )


@pytest.fixture(scope="module", params=[5, 7, 9])
def bsf_design(request):
    return synthesize_lc_bsf(
        "elliptic", order=request.param, f_low_hz=F_LOW, f_high_hz=F_HIGH, stopband_atten_db=ATTEN
    )


# ---------------------------------------------------------------------------
# The composite element itself
# ---------------------------------------------------------------------------


def test_composite_trap_matches_brute_force_admittance() -> None:
    """S21 of a lone composite shunt branch must equal 2/(2 + Z₀·Y) with
    Y computed longhand from the four element values."""
    params = {"L_s": 4e-9, "C_s": 3e-12, "L_p": 6e-9, "C_p": 2e-12}
    f = np.geomspace(1e8, 2e10, 800)
    s21 = ladder_sparams_from_components([("shunt_composite_trap", params)], f, z0=50.0)[:, 1, 0]

    w = 2.0 * np.pi * f
    s = 1j * w
    z_branch = (
        s * params["L_s"]
        + 1.0 / (s * params["C_s"])
        + s * params["L_p"] / (s**2 * params["L_p"] * params["C_p"] + 1.0)
    )
    expected = 2.0 / (2.0 + 50.0 / z_branch)
    assert np.allclose(s21, expected, rtol=1e-9)


def test_composite_trap_notches_at_its_two_roots() -> None:
    params = {"L_s": 4e-9, "C_s": 3e-12, "L_p": 6e-9, "C_p": 2e-12}
    zeros = _composite_branch_zeros_hz(params)
    assert len(zeros) == 2
    s21_db = _s21_db([("shunt_composite_trap", params)], zeros)
    assert np.all(s21_db < -60.0), f"branch should short at its roots; got {s21_db}"


def test_components_dict_pairs_composite_branch() -> None:
    """{Lk_s, Ck_s} + {Lk, Ck} at an even index form one composite trap."""
    comps = {
        "L1": 8e-9,
        "C1_s": 3e-12,
        "L2_s": 4e-9,
        "C2_s": 5e-12,
        "L2": 6e-9,
        "C2": 2e-12,
        "L3": 8e-9,
        "C3_s": 3e-12,
    }
    els = components_dict_to_elements(comps, topology="series_first", kind="bandpass")
    assert [k for k, _ in els] == ["series_lc_series", "shunt_composite_trap", "series_lc_series"]
    trap = els[1][1]
    assert trap == {"L_s": 4e-9, "C_s": 5e-12, "L_p": 6e-9, "C_p": 2e-12}


# ---------------------------------------------------------------------------
# BPF synthesis
# ---------------------------------------------------------------------------


def test_elliptic_bpf_is_a_bandpass(bpf_design) -> None:
    s21 = _s21_db(_elements(bpf_design), [F_LOW / 3, F0, F_HIGH * 3])
    assert s21[1] > -0.5, f"not passing at f0 ({s21[1]:.2f} dB)"
    assert s21[0] < -25.0, f"not rejecting below band ({s21[0]:.2f} dB)"
    assert s21[2] < -25.0, f"not rejecting above band ({s21[2]:.2f} dB)"


def test_bpf_zeros_are_geometric_pairs_straddling_the_band(bpf_design) -> None:
    zeros = bpf_design.transmission_zeros_hz
    n_traps = (bpf_design.metadata["prototype_order"]) // 2
    assert len(zeros) == 2 * n_traps, "each LPF zero must map to two BPF zeros"
    below = sorted(z for z in zeros if z < F_LOW)
    above = sorted(z for z in zeros if z > F_HIGH)
    assert len(below) == n_traps and len(above) == n_traps
    # mirror pairing: lowest-below pairs with highest-above, product = f0²
    for lo, hi in zip(below, reversed(above), strict=True):
        assert lo * hi == pytest.approx(F0**2, rel=1e-9)


def test_bpf_zeros_solve_the_root_equation(bpf_design) -> None:
    """Zeros must land exactly on ω₀(√(b²+1) ± b), b = ω_z·Δ/2."""
    expected = _mapped_zeros_hz(bpf_design.metadata["prototype_order"], "bandpass")
    assert sorted(bpf_design.transmission_zeros_hz) == pytest.approx(expected, rel=1e-9)


def test_bpf_reported_zeros_match_branch_resonances(bpf_design) -> None:
    """Math-consistency invariant, composite form: reported zeros are the
    quadratic roots of each physical branch."""
    achieved: list[float] = []
    for kind, params in _elements(bpf_design):
        if kind == "shunt_composite_trap":
            achieved.extend(_composite_branch_zeros_hz(params))
    assert sorted(achieved) == pytest.approx(sorted(bpf_design.transmission_zeros_hz), rel=1e-9)


def test_bpf_notches_are_deep_at_the_reported_zeros(bpf_design) -> None:
    s21 = _s21_db(_elements(bpf_design), bpf_design.transmission_zeros_hz)
    for z, level in zip(bpf_design.transmission_zeros_hz, s21, strict=True):
        assert level < -40.0, f"zero at {z / 1e9:.3f} GHz only {level:.1f} dB deep"


def test_bpf_equals_lpf_prototype_composed_with_the_map(bpf_design) -> None:
    """The transform is exact algebra: |S21_bpf(f)| == |S21_lpf(fc·|ω_map|)|."""
    order = bpf_design.metadata["prototype_order"]
    fc = 1e9
    lpf = synthesize_lc_lpf("elliptic", order=order, cutoff_hz=fc, stopband_atten_db=ATTEN)
    lpf_els = components_dict_to_elements(
        lpf.components, topology=str(lpf.topology), kind="lowpass"
    )

    f = np.geomspace(F0 / 5, F0 * 5, 301)
    w_map = np.abs((f / F0 - F0 / f) / DELTA)  # |ω_lpf|, normalized
    bpf_db = _s21_db(_elements(bpf_design), f)
    lpf_db = _s21_db(lpf_els, w_map * fc)
    assert np.allclose(bpf_db, lpf_db, atol=0.05)


# ---------------------------------------------------------------------------
# BSF synthesis
# ---------------------------------------------------------------------------


def test_elliptic_bsf_is_a_bandstop(bsf_design) -> None:
    s21 = _s21_db(_elements(bsf_design), [F_LOW / 3, F0, F_HIGH * 3])
    assert s21[0] > -1.0, f"not passing below band ({s21[0]:.2f} dB)"
    assert s21[2] > -1.0, f"not passing above band ({s21[2]:.2f} dB)"
    assert s21[1] < -30.0, f"not rejecting at f0 ({s21[1]:.2f} dB)"


def test_bsf_zeros_include_f0_and_trap_pairs_inside_the_band(bsf_design) -> None:
    zeros = sorted(bsf_design.transmission_zeros_hz)
    n_traps = bsf_design.metadata["prototype_order"] // 2
    assert len(zeros) == 2 * n_traps + 1, "trap pairs plus the ω₀ zero of the main-path tanks"
    assert F0 in [pytest.approx(z, rel=1e-9) for z in zeros]
    for z in zeros:
        assert F_LOW < z < F_HIGH, f"BSF zero {z / 1e6:.1f} MHz must lie inside the notch"


def test_bsf_zeros_solve_the_root_equation(bsf_design) -> None:
    """Trap zeros at ω₀(√(b²+1) ± b), b = Δ/(2·ω_z), plus ω₀ itself."""
    order = bsf_design.metadata["prototype_order"]
    expected = sorted([*_mapped_zeros_hz(order, "bandstop"), F0])
    assert sorted(bsf_design.transmission_zeros_hz) == pytest.approx(expected, rel=1e-9)


def test_bsf_reported_zeros_match_branch_resonances(bsf_design) -> None:
    achieved: list[float] = []
    f0_seen: set[float] = set()
    for kind, params in _elements(bsf_design):
        if kind == "shunt_composite_trap":
            achieved.extend(_composite_branch_zeros_hz(params))
        elif kind == "series_lc_parallel":
            f0_seen.add(round(1.0 / (2.0 * math.pi * math.sqrt(params["L"] * params["C"]))))
    assert len(f0_seen) == 1, "every main-path tank must anti-resonate at the same f0"
    achieved.append(f0_seen.pop())
    assert sorted(achieved) == pytest.approx(sorted(bsf_design.transmission_zeros_hz), rel=1e-6)


def test_bsf_notches_are_deep_at_the_reported_zeros(bsf_design) -> None:
    s21 = _s21_db(_elements(bsf_design), bsf_design.transmission_zeros_hz)
    for z, level in zip(bsf_design.transmission_zeros_hz, s21, strict=True):
        assert level < -40.0, f"zero at {z / 1e6:.1f} MHz only {level:.1f} dB deep"


def test_bsf_equals_lpf_prototype_composed_with_the_map(bsf_design) -> None:
    """|S21_bsf(f)| == |S21_lpf(fc·Δ/|f/f0 − f0/f|)| exactly."""
    order = bsf_design.metadata["prototype_order"]
    fc = 1e9
    lpf = synthesize_lc_lpf("elliptic", order=order, cutoff_hz=fc, stopband_atten_db=ATTEN)
    lpf_els = components_dict_to_elements(
        lpf.components, topology=str(lpf.topology), kind="lowpass"
    )

    f = np.geomspace(F0 / 5, F0 * 5, 301)
    f = f[np.abs(f - F0) > 0.005 * F0]  # the map diverges at f0 exactly
    w_map = DELTA / np.abs(f / F0 - F0 / f)
    bsf_db = _s21_db(_elements(bsf_design), f)
    lpf_db = _s21_db(lpf_els, w_map * fc)
    assert np.allclose(bsf_db, lpf_db, atol=0.05)


# ---------------------------------------------------------------------------
# Contract details
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("synth", [synthesize_lc_bpf, synthesize_lc_bsf])
def test_even_order_elliptic_is_rejected(synth) -> None:
    with pytest.raises(ValueError, match="odd order"):
        synth("elliptic", order=4, f_low_hz=F_LOW, f_high_hz=F_HIGH)


@pytest.mark.parametrize("synth", [synthesize_lc_bpf, synthesize_lc_bsf])
def test_elliptic_coerces_to_series_first(synth) -> None:
    """The elliptic prototype extraction is T-form only; shunt_first is
    coerced so the components dict and topology label stay consistent."""
    d = synth("elliptic", order=5, f_low_hz=F_LOW, f_high_hz=F_HIGH, topology="shunt_first")
    assert str(d.topology) == "series_first"


def test_z0_scales_the_components() -> None:
    d50 = synthesize_lc_bpf("elliptic", order=5, f_low_hz=F_LOW, f_high_hz=F_HIGH, z0=50.0)
    d75 = synthesize_lc_bpf("elliptic", order=5, f_low_hz=F_LOW, f_high_hz=F_HIGH, z0=75.0)
    s = ladder_sparams_from_components(_elements(d75), np.array([F0]), z0=75.0)
    assert 20.0 * np.log10(abs(s[0, 0, 0])) < -15.0
    assert d50.components != d75.components


# ---------------------------------------------------------------------------
# Against a real simulator (qucsator), independent of the analytical ladder.
# ---------------------------------------------------------------------------

HAS_QUCS = shutil.which("qucsator_rf") is not None or shutil.which("qucsator") is not None


def _qucsator_vs_analytic_max_dev_db(design, tmp_path) -> float:
    from mcp_qucs_s.netlist import generate_ladder_netlist
    from mcp_qucs_s.sparams import network_from_dat

    els = _elements(design)
    net = generate_ladder_netlist(
        els, tmp_path / "f.net", f_start_hz=1e8, f_stop_hz=1e10, points=400
    )
    dat = tmp_path / "f.dat"
    exe = shutil.which("qucsator_rf") or shutil.which("qucsator")
    subprocess.run([exe, "-i", str(net), "-o", str(dat)], capture_output=True, timeout=120)
    assert dat.is_file()

    nw = network_from_dat(dat)
    qs21 = 20.0 * np.log10(np.maximum(np.abs(nw.s[:, 1, 0]), 1e-12))
    analytic = _s21_db(els, nw.f)
    # Ignore bins that land essentially on a transmission zero: both models
    # agree the response is "very deep" there, but the depth itself is
    # numerically meaningless for ideal lossless elements.
    mask = analytic > -90.0
    return float(np.max(np.abs(qs21[mask] - analytic[mask])))


@pytest.mark.skipif(not HAS_QUCS, reason="qucsator not installed")
@pytest.mark.qucs
@pytest.mark.integration
def test_elliptic_bpf_matches_qucsator(bpf_design, tmp_path) -> None:
    assert _qucsator_vs_analytic_max_dev_db(bpf_design, tmp_path) < 0.05


@pytest.mark.skipif(not HAS_QUCS, reason="qucsator not installed")
@pytest.mark.qucs
@pytest.mark.integration
def test_elliptic_bsf_matches_qucsator(bsf_design, tmp_path) -> None:
    assert _qucsator_vs_analytic_max_dev_db(bsf_design, tmp_path) < 0.05
