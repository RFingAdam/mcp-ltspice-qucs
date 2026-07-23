"""Combline BPF synthesis (issue #27, final topology) on the exact
N-line TEM machinery of ``multiconductor``.

Combline: all lines shorted at the SAME end, a lumped capacitor from
each open end to ground, resonator length θ0 < 90° (default 45°). The
capacitor tunes the shorted stub — ``C = Y_r·cot(θ0)/ω0`` — and pure
TEM combline genuinely needs it (at θ0 = 90° the structure has no
passband). Everything is derived, not recalled:

- Pair split: keeping both top ends, the coupling block is the
  SAME-end (−j·cotθ) one, so det = 0 gives the exact transcendental
  ``ωC = (Y_r ± y_m)·cot(θ(ω))`` — the − root is f_l, the + root f_h.
- Slope parameter of the loaded resonator:
  ``b = (Y_r/2)(cotθ0 + θ0·csc²θ0)`` (Matthaei's b_j), giving the tap
  ``θ_t = arcsin(sinθ0·√(b/(G0·Qe)))`` which reduces to the
  interdigital formula at θ0 = 90°.
- Spurious window: the next resonance branch needs cotθ > 0 again
  (θ ∈ (π, 3π/2)), so a 45° combline is clean until ≈ 4·f0.

The exact solver (extended with ``cap_loads``) and the qucsator graph
netlist (TLIN stubs + TLIN4P connectors + ideal C elements) are two
exact models of the same network and must agree at numerical precision.
"""

from __future__ import annotations

import math
import shutil
import subprocess

import numpy as np
import pytest

from mcp_qucs_s.distributed import combline_bpf
from mcp_qucs_s.microstrip import Substrate
from mcp_qucs_s.multiconductor import combline_pair_split, segmented_array_sparams
from mcp_qucs_s.netlist import generate_coupled_array_netlist
from mcp_qucs_s.sparams import network_from_dat

HAS_QUCS = shutil.which("qucsator_rf") is not None or shutil.which("qucsator") is not None
requires_qucs = pytest.mark.skipif(not HAS_QUCS, reason="qucsator not installed")

F0 = 2.0e9
DELTA = 0.1
Z0 = 50.0
G_CHEB_05 = [1.0, 1.5963, 1.0967, 1.5963, 1.0]
ROGERS = Substrate(er=3.66, h_mm=0.508, t_um=35.0, tan_d=0.0037)
Y_R = 1.0 / 70.0


def _design():
    return combline_bpf(
        G_CHEB_05, F0, DELTA, z0=Z0, substrate=ROGERS, z_resonator_ohm=70.0, theta0_deg=45.0
    )


# ---------------------------------------------------------------------------
# Resonator tuning and pair split
# ---------------------------------------------------------------------------


def test_loading_cap_tunes_the_shorted_stub() -> None:
    """Y_r = 1/70 S, θ0 = 45° (cot = 1), f0 = 2 GHz:
    C = Y_r·cotθ0/ω0 = 1/(70·2π·2e9) = 1.1368 pF."""
    d = _design()
    c_expected = Y_R / (2.0 * math.pi * F0)
    assert c_expected == pytest.approx(1.1368e-12, rel=1e-3)
    for r in d["resonators"]:
        assert r["c_load_farad"] == pytest.approx(c_expected, rel=1e-12)


def test_pair_split_brackets_f0_and_grows_with_coupling() -> None:
    prev = 0.0
    for r_frac in (0.05, 0.1, 0.2):
        fl, fh = combline_pair_split(Y_R, r_frac * Y_R, 45.0, F0)
        assert fl < F0 < fh
        k = (fh**2 - fl**2) / (fh**2 + fl**2)
        assert k > prev
        prev = k
    fl, fh = combline_pair_split(Y_R, 1e-9 * Y_R, 45.0, F0)
    assert fl == pytest.approx(F0, rel=1e-6)
    assert fh == pytest.approx(F0, rel=1e-6)


def test_solver_cap_load_resonates_the_line_at_f0() -> None:
    """One shorted 45° line with its tuning cap: the shunt branch's
    admittance vanishes at f0, so S11 of a port at the top crosses
    +1 (zero phase) exactly there."""
    c_load = Y_R / (2.0 * math.pi * F0)
    f = np.linspace(0.99 * F0, 1.01 * F0, 201)
    s = segmented_array_sparams(
        np.array([[Y_R]]),
        f,
        F0,
        segments_deg=[45.0],
        bottom=["short"],
        top=["port"],
        cap_loads=[(1, 0, c_load)],
        z0_system=Z0,
    )
    phase = np.unwrap(np.angle(s[:, 0, 0]))
    crossing = f[int(np.argmin(np.abs(phase)))]
    assert crossing == pytest.approx(F0, rel=1e-3)
    assert np.allclose(np.abs(s[:, 0, 0]), 1.0, atol=1e-9), "lossless 1-port"


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------


def test_design_structure_all_shorts_same_end() -> None:
    d = _design()
    assert len(d["resonators"]) == 3
    assert all(r["shorted_end"] == "bottom" for r in d["resonators"])
    assert d["theta0_deg"] == pytest.approx(45.0)
    assert sum(d["segments_deg"]) == pytest.approx(45.0)


def test_couplings_hit_the_prototype_targets() -> None:
    d = _design()
    assert len(d["couplings"]) == 2
    for i, c in enumerate(d["couplings"]):
        k_target = DELTA / math.sqrt(G_CHEB_05[i + 1] * G_CHEB_05[i + 2])
        assert c["k"] == pytest.approx(k_target, rel=1e-9)
        fl, fh = combline_pair_split(Y_R, c["y_mutual"], 45.0, F0)
        assert (fh**2 - fl**2) / (fh**2 + fl**2) == pytest.approx(k_target, rel=1e-6)


def test_tap_reduces_to_interdigital_formula_at_90_degrees() -> None:
    """At θ0 = 90° the combline slope parameter is (π/4)·Y_r and the tap
    formula must collapse to the interdigital one."""
    qe = G_CHEB_05[0] * G_CHEB_05[1] / DELTA
    b_90 = (Y_R / 2.0) * (math.cos(math.pi / 2) / math.sin(math.pi / 2) + (math.pi / 2) / 1.0)
    assert b_90 == pytest.approx((math.pi / 4.0) * Y_R, rel=1e-12)
    theta_inter = math.degrees(math.asin(math.sqrt(math.pi * Y_R / (4.0 * (1 / Z0) * qe))))
    theta_comb = math.degrees(math.asin(1.0 * math.sqrt(b_90 / ((1 / Z0) * qe))))
    assert theta_comb == pytest.approx(theta_inter, rel=1e-12)


def test_tap_point_matches_slope_parameter_formula() -> None:
    d = _design()
    qe = G_CHEB_05[0] * G_CHEB_05[1] / DELTA
    t0 = math.pi / 4.0
    b = (Y_R / 2.0) * (math.cos(t0) / math.sin(t0) + t0 / math.sin(t0) ** 2)
    theta_t = math.degrees(math.asin(math.sin(t0) * math.sqrt(b / ((1 / Z0) * qe))))
    assert d["tap_deg"] == pytest.approx(theta_t, rel=1e-9)
    assert 0.0 < d["tap_deg"] < 45.0


def test_too_wide_bandwidth_is_rejected() -> None:
    with pytest.raises(ValueError, match=r"[Uu]nrealizable|stub|tap"):
        combline_bpf(
            G_CHEB_05, F0, 0.9, z0=Z0, substrate=ROGERS, z_resonator_ohm=70.0, theta0_deg=45.0
        )


def test_design_self_reports_achieved_metrics() -> None:
    a = _design()["achieved"]
    assert a["band_center_hz"] == pytest.approx(F0, rel=0.03)
    assert a["bw_3db_frac"] == pytest.approx(DELTA, rel=0.4)
    assert a["peak_db"] > -0.5


def test_response_is_a_bandpass_with_clean_upper_stopband() -> None:
    """The 45° combline's next resonance branch sits near 4·f0 — the
    stopband from 1.5·f0 to 3·f0 must stay deep (the topology's selling
    point vs edge-coupled, whose spurious sits at 2·f0)."""
    d = _design()
    f = np.linspace(0.5e9, 6.0e9, 2401)
    s = segmented_array_sparams(
        d["y_c"],
        f,
        F0,
        segments_deg=d["segments_deg"],
        bottom=d["bottom"],
        top=d["top"],
        ports=d["ports"],
        cap_loads=d["cap_loads"],
        z0_system=Z0,
    )
    s21 = 20.0 * np.log10(np.maximum(np.abs(s[:, 1, 0]), 1e-12))
    i0 = int(np.argmin(np.abs(f - F0)))
    assert s21[i0] > -1.5, f"midband {s21[i0]:.2f} dB"
    upper = s21[(f > 1.5 * F0) & (f < 3.0 * F0)]
    assert np.all(upper < -30.0), f"upper stopband max {upper.max():.1f} dB"
    lower = s21[f < 0.6 * F0]
    assert np.all(lower < -20.0)


# ---------------------------------------------------------------------------
# Graph netlist + real qucsator
# ---------------------------------------------------------------------------


def test_netlist_emits_caps_at_top_nodes(tmp_path) -> None:
    d = _design()
    text = generate_coupled_array_netlist(
        d, tmp_path / "cb.net", f_start_hz=1e9, f_stop_hz=3e9, points=101, sweep="lin"
    ).read_text()
    caps = [ln for ln in text.splitlines() if ln.startswith("C:")]
    assert len(caps) == 3, "one loading cap per resonator"
    assert all("gnd" in ln for ln in caps)
    n_seg = len(d["segments_deg"])
    stubs = [ln for ln in text.splitlines() if ln.startswith("TLIN:")]
    floats = [ln for ln in text.splitlines() if ln.startswith("TLIN4P:")]
    assert len(stubs) == 3 * n_seg
    assert len(floats) == 2 * n_seg


@requires_qucs
@pytest.mark.qucs
@pytest.mark.integration
def test_graph_netlist_matches_exact_solver(tmp_path) -> None:
    d = _design()
    net = generate_coupled_array_netlist(
        d, tmp_path / "cb.net", f_start_hz=1.4e9, f_stop_hz=2.6e9, points=401, sweep="lin"
    )
    dat = tmp_path / "cb.dat"
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
        cap_loads=d["cap_loads"],
        z0_system=Z0,
    )
    qs21 = 20.0 * np.log10(np.maximum(np.abs(nw.s[:, 1, 0]), 1e-12))
    a21 = 20.0 * np.log10(np.maximum(np.abs(s[:, 1, 0]), 1e-12))
    mask = a21 > -90.0
    assert float(np.max(np.abs(qs21[mask] - a21[mask]))) < 0.001


# ---------------------------------------------------------------------------
# MCP tool envelope
# ---------------------------------------------------------------------------


def test_synthesize_combline_bpf_tool() -> None:
    from mcp_qucs_s import server

    env = server.synthesize_combline_bpf(
        g_coefficients=G_CHEB_05,
        f0_hz=F0,
        fractional_bandwidth=DELTA,
        substrate={"er": 3.66, "h_mm": 0.508, "tan_d": 0.0037},
    )
    assert env.status == "ok"
    assert len(env.data["resonators"]) == 3
    assert env.data["resonators"][0]["c_load_farad"] == pytest.approx(1.1368e-12, rel=1e-3)


def test_synthesize_combline_bpf_tool_error_envelope() -> None:
    from mcp_qucs_s import server

    env = server.synthesize_combline_bpf(
        g_coefficients=[1.0, 1.5963],
        f0_hz=F0,
        fractional_bandwidth=DELTA,
        substrate={"er": 3.66, "h_mm": 0.508},
    )
    assert env.status == "error"
    assert "at least" in env.error
