"""Interdigital BPF synthesis (issue #27) built on exact N-line TEM
array machinery.

The N-conductor obstacle (a physical line cannot sit in two MCOUPLED
elements) is bypassed exactly: a same-velocity TEM array's 2N-port
Y-matrix is linear in the characteristic-admittance matrix Y_c, so a
tridiagonal array decomposes into ordinary TLIN stubs (one per line,
admittance Y_ii − Σ|mutuals|) plus floating TLIN4P connector lines (one
per coupling, admittance y_m) — probe-verified this session to
reproduce the CTLIN coupled section at 0.0000 mdB. That makes arbitrary
interdigital arrays netlistable in qucsator exactly, and the same
matrix termination gives an exact analytical solver.

Design values carry no book-table recall risk: k_{i,i+1} = Δ/√(g_i·g_j)
and Qe = g0·g1/Δ (standard coupled-resonator identities), the pair
resonance split is closed form (det ⇒ cosθ = ±y_m/Y_r ⇒
f_l/f0 = arccos(r)/(π/2), f_h = 2f0 − f_l), and the tap point comes
from the shorted-λ/4 slope parameter b = (π/4)·Y_r:
θ_t = arcsin(√(π·Y_r/(4·G0·Qe))).
"""

from __future__ import annotations

import itertools
import math
import shutil
import subprocess

import numpy as np
import pytest

from mcp_qucs_s.distributed import coupled_section_sparams, interdigital_bpf
from mcp_qucs_s.microstrip import Substrate
from mcp_qucs_s.multiconductor import (
    interdigital_pair_k,
    mutual_for_k,
    segmented_array_sparams,
)
from mcp_qucs_s.netlist import generate_interdigital_netlist
from mcp_qucs_s.sparams import network_from_dat

HAS_QUCS = shutil.which("qucsator_rf") is not None or shutil.which("qucsator") is not None
requires_qucs = pytest.mark.skipif(not HAS_QUCS, reason="qucsator not installed")

F0 = 2.0e9
DELTA = 0.1
Z0 = 50.0
G_CHEB_05 = [1.0, 1.5963, 1.0967, 1.5963, 1.0]
ROGERS = Substrate(er=3.66, h_mm=0.508, t_um=35.0, tan_d=0.0037)


def _design():
    return interdigital_bpf(G_CHEB_05, F0, DELTA, z0=Z0, substrate=ROGERS, z_resonator_ohm=70.0)


# ---------------------------------------------------------------------------
# Pair coupling closed form
# ---------------------------------------------------------------------------


def test_pair_k_closed_form_matches_hand_values() -> None:
    """r = y_m/Y_r = 0.2: θ_l = arccos(0.2) = 78.463°, f_l = 0.87181·f0,
    f_h = 1.12819·f0, k = (f_h² − f_l²)/(f_h² + f_l²) = 0.25223."""
    k = interdigital_pair_k(0.2)
    fl = math.acos(0.2) / (math.pi / 2)
    fh = 2.0 - fl
    assert k == pytest.approx((fh**2 - fl**2) / (fh**2 + fl**2), rel=1e-12)
    assert k == pytest.approx(0.25223, abs=5e-5)


def test_pair_k_is_monotone_from_zero() -> None:
    assert interdigital_pair_k(0.0) == pytest.approx(0.0, abs=1e-12)
    rs = np.linspace(0.01, 0.8, 40)
    ks = [interdigital_pair_k(float(r)) for r in rs]
    assert all(b > a for a, b in itertools.pairwise(ks))


def test_mutual_for_k_round_trips() -> None:
    for k_target in (0.02, 0.06, 0.25):
        r = mutual_for_k(k_target)
        assert interdigital_pair_k(r) == pytest.approx(k_target, rel=1e-9)


# ---------------------------------------------------------------------------
# Exact segmented-array solver
# ---------------------------------------------------------------------------


def test_solver_reproduces_the_coupled_bpf_section() -> None:
    """N=2 array, diagonal ports, others open, no shorts — must equal the
    closed-form coupled-section cascade at numerical precision."""
    ze, zo = 70.61, 39.24
    ym = (1.0 / zo - 1.0 / ze) / 2.0
    y_self = 1.0 / ze + ym
    y_c = np.array([[y_self, -ym], [-ym, y_self]])
    f = np.linspace(1.0e9, 3.0e9, 301)
    s = segmented_array_sparams(
        y_c,
        f,
        F0,
        segments_deg=[90.0],
        bottom=["port", "open"],
        top=["open", "port"],
        z0_system=Z0,
    )
    ref = coupled_section_sparams([(ze, zo)], f, F0, z0_system=Z0)
    assert np.max(np.abs(s - ref)) < 1e-9


def test_solver_is_reciprocal_and_lossless() -> None:
    y_c = np.array(
        [
            [1 / 70.0, -0.002, 0.0],
            [-0.002, 1 / 70.0, -0.003],
            [0.0, -0.003, 1 / 70.0],
        ]
    )
    f = np.linspace(1.5e9, 2.5e9, 101)
    s = segmented_array_sparams(
        y_c,
        f,
        F0,
        segments_deg=[30.0, 60.0],
        bottom=["short", "open", "short"],
        top=["open", "short", "open"],
        ports=[(1, 0), (1, 2)],
        z0_system=Z0,
    )
    assert np.allclose(s[:, 0, 1], s[:, 1, 0], atol=1e-10)
    power = np.abs(s[:, 0, 0]) ** 2 + np.abs(s[:, 1, 0]) ** 2
    assert np.allclose(power, 1.0, atol=1e-8), "lossless TEM network must conserve power"


# ---------------------------------------------------------------------------
# Interdigital synthesis
# ---------------------------------------------------------------------------


def test_design_structure_and_alternating_shorts() -> None:
    d = _design()
    res = d["resonators"]
    assert len(res) == 3
    assert [r["shorted_end"] for r in res] == ["bottom", "top", "bottom"]
    assert all(r["z_ohm"] == pytest.approx(70.0) for r in res)


def test_couplings_hit_the_prototype_targets() -> None:
    d = _design()
    cps = d["couplings"]
    assert len(cps) == 2
    for i, c in enumerate(cps):
        k_target = DELTA / math.sqrt(G_CHEB_05[i + 1] * G_CHEB_05[i + 2])
        assert c["k"] == pytest.approx(k_target, rel=1e-9)
        assert interdigital_pair_k(c["y_mutual"] * 70.0) == pytest.approx(k_target, rel=1e-6)


def test_stub_admittances_stay_positive() -> None:
    d = _design()
    for r in d["resonators"]:
        assert r["y_stub"] > 0.0


def test_tap_point_matches_slope_parameter_formula() -> None:
    d = _design()
    qe = G_CHEB_05[0] * G_CHEB_05[1] / DELTA
    theta_t = math.degrees(math.asin(math.sqrt(math.pi * (1 / 70.0) / (4.0 * (1 / Z0) * qe))))
    assert d["tap_deg"] == pytest.approx(theta_t, rel=1e-9)
    assert 0.0 < d["tap_deg"] < 45.0


def test_too_wide_bandwidth_is_rejected() -> None:
    """The interior resonator's stub admittance is Y_r·(1 − r_left −
    r_right); each r solves k(r) = Δ/√(g·g), so the stub crosses zero
    near Δ ≈ 0.79 on 70 Ω resonators — Δ = 0.85 must be refused."""
    with pytest.raises(ValueError, match=r"[Uu]nrealizable|stub"):
        interdigital_bpf(G_CHEB_05, F0, 0.85, z0=Z0, substrate=ROGERS, z_resonator_ohm=70.0)


def test_physical_dimensions_present_with_notes() -> None:
    d = _design()
    for c in d["couplings"]:
        assert c["gap_mm"] > 0.02
        assert c["width_mm"] > 0.05
    notes = " ".join(d["notes"]).lower()
    assert "tap" in notes
    assert "per-pair" in notes or "first-cut" in notes


def test_design_self_reports_achieved_metrics() -> None:
    """The tapped-feed approximation degrades ripple vs the prototype —
    the design must report what it actually achieves on the exact model
    instead of implying the spec."""
    a = _design()["achieved"]
    assert a["band_center_hz"] == pytest.approx(F0, rel=0.02)
    assert a["bw_3db_frac"] == pytest.approx(DELTA, rel=0.35)
    assert a["peak_db"] > -0.2
    assert -12.0 < a["worst_inband_return_loss_db"] < -3.0


def test_response_is_a_bandpass_meeting_spec() -> None:
    d = _design()
    f = np.linspace(1.4e9, 2.6e9, 1201)
    s = segmented_array_sparams(
        d["y_c"],
        f,
        F0,
        segments_deg=d["segments_deg"],
        bottom=d["bottom"],
        top=d["top"],
        ports=d["ports"],
        z0_system=Z0,
    )
    s21 = 20.0 * np.log10(np.maximum(np.abs(s[:, 1, 0]), 1e-12))
    i0 = int(np.argmin(np.abs(f - F0)))
    assert s21[i0] > -1.0, f"midband {s21[i0]:.2f} dB"
    out = s21[np.abs(f - F0) > 0.35e9]
    assert np.all(out < -20.0), "stopbands must hold"
    above = f[s21 > -3.0]
    bw = (above[-1] - above[0]) / F0
    assert bw == pytest.approx(DELTA, rel=0.35), f"-3 dB bandwidth {bw:.3f} vs Δ={DELTA}"


# ---------------------------------------------------------------------------
# Graph netlist + real qucsator
# ---------------------------------------------------------------------------


def test_netlist_structure(tmp_path) -> None:
    d = _design()
    text = generate_interdigital_netlist(
        d, tmp_path / "id.net", f_start_hz=1e9, f_stop_hz=3e9, points=101, sweep="lin"
    ).read_text()
    stubs = [ln for ln in text.splitlines() if ln.startswith("TLIN:")]
    floats = [ln for ln in text.splitlines() if ln.startswith("TLIN4P:")]
    n_seg = len(d["segments_deg"])
    assert len(stubs) == 3 * n_seg
    assert len(floats) == 2 * n_seg
    assert "gnd" in stubs[0] or any("gnd" in ln for ln in stubs), "shorts must land on gnd"
    assert 'Num="1"' in text and 'Num="2"' in text


@requires_qucs
@pytest.mark.qucs
@pytest.mark.integration
def test_graph_netlist_matches_exact_solver(tmp_path) -> None:
    """Both sides are exact models of the same TEM network — agreement
    must be at numerical precision."""
    d = _design()
    net = generate_interdigital_netlist(
        d, tmp_path / "id.net", f_start_hz=1.4e9, f_stop_hz=2.6e9, points=401, sweep="lin"
    )
    dat = tmp_path / "id.dat"
    exe = shutil.which("qucsator_rf") or shutil.which("qucsator")
    subprocess.run(
        [exe, "-i", str(net), "-o", str(dat)],
        capture_output=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    assert dat.is_file()
    nw = network_from_dat(dat)
    s = segmented_array_sparams(
        d["y_c"],
        nw.f,
        F0,
        segments_deg=d["segments_deg"],
        bottom=d["bottom"],
        top=d["top"],
        ports=d["ports"],
        z0_system=Z0,
    )
    qs21 = 20.0 * np.log10(np.maximum(np.abs(nw.s[:, 1, 0]), 1e-12))
    a21 = 20.0 * np.log10(np.maximum(np.abs(s[:, 1, 0]), 1e-12))
    mask = a21 > -90.0
    assert float(np.max(np.abs(qs21[mask] - a21[mask]))) < 0.001


# ---------------------------------------------------------------------------
# MCP tool envelope
# ---------------------------------------------------------------------------


def test_synthesize_interdigital_bpf_tool() -> None:
    from mcp_qucs_s import server

    env = server.synthesize_interdigital_bpf(
        g_coefficients=G_CHEB_05,
        f0_hz=F0,
        fractional_bandwidth=DELTA,
        substrate={"er": 3.66, "h_mm": 0.508, "tan_d": 0.0037},
    )
    assert env.status == "ok"
    assert len(env.data["resonators"]) == 3
    assert len(env.data["couplings"]) == 2


def test_synthesize_interdigital_bpf_tool_error_envelope() -> None:
    from mcp_qucs_s import server

    env = server.synthesize_interdigital_bpf(
        g_coefficients=[1.0, 1.5963],
        f0_hz=F0,
        fractional_bandwidth=DELTA,
        substrate={"er": 3.66, "h_mm": 0.508},
    )
    assert env.status == "error"
    assert "at least" in env.error
