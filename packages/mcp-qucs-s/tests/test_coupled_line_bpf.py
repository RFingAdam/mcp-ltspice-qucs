"""Edge-coupled (parallel coupled-line) BPF synthesis (issue #27) — the
coupled-section core that hairpin / interdigital / combline build on.

Electrical synthesis is exact closed form (Pozar §8.7, eq. 8.121):
prototype g's → J-inverter constants → per-section even/odd impedances,
each section a quarter-wave at f₀. The reference pin is Pozar 4e
Example 8.7 (N=3, 0.5 dB equal-ripple, f₀ = 2 GHz, Δ = 0.1):
Z₀e/Z₀o = 70.61/39.24, 56.64/44.77, 56.64/44.77, 70.61/39.24 Ω.

Physical realization is quasi-static Garg-Bahl coupled-microstrip
analysis (even: C_p + C_f + C_f'; odd: C_p + C_f + C_ga + C_gd, exact
elliptic-integral ratio via scipy) inverted numerically for (W, S).

Simulator ground truth (probe-verified this session): qucsator's CTLIN
is the ideal coupled line — vacuum velocity, node order line1-near /
line1-far / line2-far / line2-near — and the diagonal-connected section
(2 & 4 open) matches the closed-form section ABCD to 0.0000 mdB, so the
ideal cascade must agree with qucsator at numerical precision. MCOUPLED
(Kirschning) is the real-model check for the synthesized W/S/L.
"""

from __future__ import annotations

import shutil
import subprocess

import numpy as np
import pytest

from mcp_qucs_s.coupled_microstrip import (
    analyze_coupled_microstrip,
    synthesize_coupled_microstrip,
)
from mcp_qucs_s.distributed import coupled_line_bpf, coupled_section_sparams
from mcp_qucs_s.microstrip import Substrate
from mcp_qucs_s.netlist import (
    generate_coupled_microstrip_netlist,
    generate_ladder_netlist,
)
from mcp_qucs_s.sparams import network_from_dat

HAS_QUCS = shutil.which("qucsator_rf") is not None or shutil.which("qucsator") is not None
requires_qucs = pytest.mark.skipif(not HAS_QUCS, reason="qucsator not installed")

F0 = 2.0e9
DELTA = 0.1
Z0 = 50.0
# Pozar 4e Example 8.7: N=3, 0.5 dB equal-ripple prototype
G_CHEB_05 = [1.0, 1.5963, 1.0967, 1.5963, 1.0]
# Published even/odd impedances for that design (Table in Ex 8.7)
POZAR_ZE_ZO = [(70.61, 39.24), (56.64, 44.77), (56.64, 44.77), (70.61, 39.24)]

FR4 = Substrate(er=4.2, h_mm=1.58, t_um=35.0, tan_d=0.02)


def _design(substrate=FR4):
    return coupled_line_bpf(G_CHEB_05, F0, DELTA, z0=Z0, substrate=substrate)


# ---------------------------------------------------------------------------
# Electrical synthesis (exact closed form)
# ---------------------------------------------------------------------------


def test_pozar_example_8_7_even_odd_impedances() -> None:
    sections = _design()["sections"]
    assert len(sections) == 4, "order N needs N+1 coupled sections"
    got = [(s["z0e_ohm"], s["z0o_ohm"]) for s in sections]
    for (ge, go), (ee, eo) in zip(got, POZAR_ZE_ZO, strict=True):
        assert ge == pytest.approx(ee, abs=0.05)
        assert go == pytest.approx(eo, abs=0.05)


def test_sections_are_quarter_wave_and_symmetric() -> None:
    sections = _design()["sections"]
    assert all(s["electrical_length_deg"] == pytest.approx(90.0) for s in sections)
    assert sections[0]["z0e_ohm"] == pytest.approx(sections[-1]["z0e_ohm"], rel=1e-12)
    assert sections[1]["z0o_ohm"] == pytest.approx(sections[-2]["z0o_ohm"], rel=1e-12)


def test_invalid_inputs_rejected() -> None:
    with pytest.raises(ValueError, match="at least"):
        coupled_line_bpf([1.0, 1.5963], F0, DELTA, z0=Z0, substrate=FR4)
    with pytest.raises(ValueError, match="fractional_bandwidth"):
        coupled_line_bpf(G_CHEB_05, F0, 0.0, z0=Z0, substrate=FR4)


# ---------------------------------------------------------------------------
# Analytical response (ideal coupled sections)
# ---------------------------------------------------------------------------


def _s21_db(design, freqs):
    secs = [(s["z0e_ohm"], s["z0o_ohm"]) for s in design["sections"]]
    s = coupled_section_sparams(secs, np.asarray(freqs, dtype=float), F0, z0_system=Z0)
    return 20.0 * np.log10(np.maximum(np.abs(s[:, 1, 0]), 1e-12))


def test_single_section_image_impedance_identity() -> None:
    """At θ = 90° a lone section's image impedance is (Z0e−Z0o)/2 — with
    the system Z₀ set to exactly that, the section must be reflectionless."""
    ze, zo = 70.61, 39.24
    zi = (ze - zo) / 2.0
    s = coupled_section_sparams([(ze, zo)], np.array([F0]), F0, z0_system=zi)
    assert abs(s[0, 0, 0]) < 1e-9
    assert abs(s[0, 1, 0]) == pytest.approx(1.0, abs=1e-9)


def test_response_is_a_bandpass_with_equal_ripple() -> None:
    d = _design()
    f = np.linspace(1.6e9, 2.4e9, 801)
    s21 = _s21_db(d, f)
    i0 = int(np.argmin(np.abs(f - F0)))
    assert s21[i0] > -0.6, f"center of the 0.5 dB-ripple passband: {s21[i0]:.2f} dB"
    in_band = (f > F0 * (1 - 0.35 * DELTA)) & (f < F0 * (1 + 0.35 * DELTA))
    assert np.all(s21[in_band] > -0.7), "ripple must stay near the 0.5 dB spec mid-band"
    out = _s21_db(d, [0.5 * F0, 1.5 * F0])
    assert np.all(out < -25.0), f"stopband too shallow: {out}"


def test_ripple_band_edges_near_design_bandwidth() -> None:
    """The equal-ripple band should span ≈ f0·(1 ± Δ/2)."""
    d = _design()
    f = np.linspace(1.7e9, 2.3e9, 4001)
    s21 = _s21_db(d, f)
    above = s21 > -0.55  # ripple level + small numeric slack
    edges = f[above]
    bw = (edges[-1] - edges[0]) / F0
    assert bw == pytest.approx(DELTA, rel=0.25), f"ripple bandwidth {bw:.3f} vs Δ={DELTA}"


def test_cascade_is_reciprocal_and_lossless() -> None:
    d = _design()
    f = np.linspace(1.0e9, 3.0e9, 200)
    secs = [(s["z0e_ohm"], s["z0o_ohm"]) for s in d["sections"]]
    s = coupled_section_sparams(secs, f, F0, z0_system=Z0)
    assert np.allclose(s[:, 0, 1], s[:, 1, 0], rtol=1e-9)
    power = np.abs(s[:, 0, 0]) ** 2 + np.abs(s[:, 1, 0]) ** 2
    assert np.allclose(power, 1.0, atol=1e-6)


# ---------------------------------------------------------------------------
# Coupled-microstrip physical model (Garg-Bahl quasi-static)
# ---------------------------------------------------------------------------


def test_analysis_brackets_the_single_line() -> None:
    """Z0e > Z0(single) > Z0o always; both even εeff and odd εeff sit
    between 1 and εr."""
    from mcp_qucs_s.microstrip import analyze_microstrip

    r = analyze_coupled_microstrip(2.8, 0.5, FR4)
    single = analyze_microstrip(2.8, FR4, 1e9)["z0_ohm"]
    assert r["z0e_ohm"] > single > r["z0o_ohm"]
    assert 1.0 < r["er_eff_o"] < FR4.er
    assert 1.0 < r["er_eff_e"] < FR4.er


def test_wider_gap_weakens_coupling() -> None:
    tight = analyze_coupled_microstrip(2.8, 0.2, FR4)
    loose = analyze_coupled_microstrip(2.8, 2.0, FR4)
    ratio_tight = tight["z0e_ohm"] / tight["z0o_ohm"]
    ratio_loose = loose["z0e_ohm"] / loose["z0o_ohm"]
    assert ratio_tight > ratio_loose > 1.0
    # decoupled limit: both modes approach the single-line impedance
    assert abs(loose["z0e_ohm"] - loose["z0o_ohm"]) < abs(tight["z0e_ohm"] - tight["z0o_ohm"])


def test_synthesis_round_trips_through_analysis() -> None:
    for ze, zo in POZAR_ZE_ZO[:2]:
        w_mm, s_mm = synthesize_coupled_microstrip(ze, zo, FR4)
        r = analyze_coupled_microstrip(w_mm, s_mm, FR4)
        assert r["z0e_ohm"] == pytest.approx(ze, rel=5e-3)
        assert r["z0o_ohm"] == pytest.approx(zo, rel=5e-3)


def test_unrealizable_coupling_is_rejected() -> None:
    """A 3 dB coupler-grade Ze/Zo ratio is beyond edge-coupled microstrip
    on this substrate — the synthesis must say so, not return a corner."""
    with pytest.raises(ValueError, match=r"[Uu]nrealizable|converge"):
        synthesize_coupled_microstrip(121.0, 21.0, FR4)


def test_design_carries_physical_dimensions() -> None:
    d = _design()
    for s in d["sections"]:
        assert s["width_mm"] > 0.05
        assert s["gap_mm"] > 0.02
        assert s["length_mm"] > 5.0  # λ/4 at 2 GHz on FR-4 is ~20 mm
        assert 1.0 < s["er_eff_e"] < FR4.er
        assert 1.0 < s["er_eff_o"] < FR4.er


# ---------------------------------------------------------------------------
# Netlist emission
# ---------------------------------------------------------------------------


def test_coupled_section_element_emits_diagonal_ctlin(tmp_path) -> None:
    text = generate_ladder_netlist(
        [
            (
                "coupled_line_section",
                {"z0e_ohm": 70.61, "z0o_ohm": 39.24, "theta_deg": 90.0, "f_ref_hz": 2e9},
            )
        ],
        tmp_path / "cs.net",
    ).read_text()
    cl = next(ln for ln in text.splitlines() if ln.startswith("CTLIN:"))
    assert 'Ze="70.61 Ohm"' in cl and 'Zo="39.24 Ohm"' in cl
    assert 'L="0.03747405725' in cl  # vacuum λ/4 at 2 GHz, metres (c/8e9)
    nodes = cl.split()[1:5]
    assert nodes[0] == "_p1", "input at line1-near"
    p2 = next(ln for ln in text.splitlines() if 'Num="2"' in ln).split()[1]
    assert p2 == nodes[2], "output must take the diagonal (line2-far) node"
    assert nodes[1] != p2 and nodes[3] != p2, "line1-far and line2-near stay open"


def test_coupled_microstrip_netlist_chains_diagonally(tmp_path) -> None:
    text = generate_coupled_microstrip_netlist(
        [
            {"width_mm": 1.2, "gap_mm": 0.3, "length_mm": 20.0},
            {"width_mm": 2.5, "gap_mm": 1.1, "length_mm": 20.5},
        ],
        FR4,
        tmp_path / "mc.net",
    ).read_text()
    assert len([ln for ln in text.splitlines() if ln.startswith("SUBST:")]) == 1
    mcs = [ln for ln in text.splitlines() if ln.startswith("MCOUPLED:")]
    assert len(mcs) == 2
    assert 'W="1.2 mm"' in mcs[0] and 'S="0.3 mm"' in mcs[0] and 'L="20 mm"' in mcs[0]
    # diagonal chaining: section 1's line2-far node is section 2's line1-near
    assert mcs[0].split()[3] == mcs[1].split()[1]
    p1 = next(ln for ln in text.splitlines() if 'Num="1"' in ln).split()[1]
    p2 = next(ln for ln in text.splitlines() if 'Num="2"' in ln).split()[1]
    assert p1 == mcs[0].split()[1] and p2 == mcs[1].split()[3]


# ---------------------------------------------------------------------------
# Against real qucsator
# ---------------------------------------------------------------------------


def _run_qucsator(net, dat) -> None:
    exe = shutil.which("qucsator_rf") or shutil.which("qucsator")
    subprocess.run([exe, "-i", str(net), "-o", str(dat)], capture_output=True, timeout=120)
    assert dat.is_file()


@requires_qucs
@pytest.mark.qucs
@pytest.mark.integration
def test_ideal_ctlin_cascade_matches_analytical(tmp_path) -> None:
    d = _design()
    elements = [
        (
            "coupled_line_section",
            {"z0e_ohm": s["z0e_ohm"], "z0o_ohm": s["z0o_ohm"], "theta_deg": 90.0, "f_ref_hz": F0},
        )
        for s in d["sections"]
    ]
    net = generate_ladder_netlist(
        elements, tmp_path / "cc.net", f_start_hz=1e9, f_stop_hz=3e9, points=401, sweep="lin"
    )
    dat = tmp_path / "cc.dat"
    _run_qucsator(net, dat)
    nw = network_from_dat(dat)
    qs21 = 20.0 * np.log10(np.maximum(np.abs(nw.s[:, 1, 0]), 1e-12))
    analytic = _s21_db(d, nw.f)
    mask = analytic > -90.0
    assert float(np.max(np.abs(qs21[mask] - analytic[mask]))) < 0.001


@requires_qucs
@pytest.mark.qucs
@pytest.mark.integration
def test_mcoupled_realization_centers_near_design(tmp_path) -> None:
    """The synthesized W/S/L through qucsator's real Kirschning coupled
    microstrip model: passband centred near f₀ with a usable passband and
    real stopbands. Run on a low-loss Rogers-class laminate so dielectric
    loss doesn't mask the shape (on FR-4 the classical midband-loss
    estimate 4.343·Σg/(Δ·Qu) is ≈ 4 dB and was measured at 4.05 dB —
    consistent physics, but a poor canvas for asserting the response).
    Quasi-static synthesis vs the dispersive model costs a few percent —
    bounds are honest, not decorative."""
    rogers = Substrate(er=3.66, h_mm=0.508, t_um=35.0, tan_d=0.0037)
    d = _design(substrate=rogers)
    net = generate_coupled_microstrip_netlist(
        [
            {"width_mm": s["width_mm"], "gap_mm": s["gap_mm"], "length_mm": s["length_mm"]}
            for s in d["sections"]
        ],
        rogers,
        tmp_path / "mc.net",
        f_start_hz=1e9,
        f_stop_hz=3e9,
        points=801,
        sweep="lin",
    )
    dat = tmp_path / "mc.dat"
    _run_qucsator(net, dat)
    nw = network_from_dat(dat)
    s21 = 20.0 * np.log10(np.maximum(np.abs(nw.s[:, 1, 0]), 1e-12))
    f_peak = nw.f[int(np.argmax(s21))]
    assert abs(f_peak - F0) / F0 < 0.05, f"passband centred at {f_peak / 1e9:.3f} GHz"
    assert s21.max() > -2.0, f"in-band loss {s21.max():.2f} dB"
    lo = s21[int(np.argmin(np.abs(nw.f - 0.5 * F0)))]
    hi = s21[int(np.argmin(np.abs(nw.f - 1.5 * F0)))]
    assert lo < -25.0 and hi < -25.0, f"stopbands: {lo:.1f} / {hi:.1f} dB"


# ---------------------------------------------------------------------------
# MCP tool envelope
# ---------------------------------------------------------------------------


def test_synthesize_coupled_line_bpf_tool() -> None:
    from mcp_qucs_s import server

    env = server.synthesize_coupled_line_bpf(
        g_coefficients=G_CHEB_05,
        f0_hz=F0,
        fractional_bandwidth=DELTA,
        substrate={"er": 4.2, "h_mm": 1.58},
    )
    assert env.status == "ok"
    assert env.data["n_sections"] == 4
    ze = [s["z0e_ohm"] for s in env.data["sections"]]
    assert ze[0] == pytest.approx(70.61, abs=0.05)


def test_synthesize_coupled_line_bpf_tool_error_envelope() -> None:
    from mcp_qucs_s import server

    env = server.synthesize_coupled_line_bpf(
        g_coefficients=[1.0, 1.5963],
        f0_hz=F0,
        fractional_bandwidth=DELTA,
        substrate={"er": 4.2, "h_mm": 1.58},
    )
    assert env.status == "error"
    assert "at least" in env.error
