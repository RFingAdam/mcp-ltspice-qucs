"""Hairpin BPF synthesis (issue #27) — the folded edge-coupled filter
(Cristal & Frankel's hairpin-line).

Folding is a circuit-graph no-op apart from the U-bend connector: each
half-wave resonator (line2 of coupled section i + line1 of section i+1)
gains the bend's electrical length θ_b, so every coupled section is
shortened to θ = 90° − θ_b/2, which keeps every resonator at exactly
180° at f₀ (uniform-bend closed form — one bend length, at the mean arm
width, for all resonators). The bend is modeled as an MLIN between the
internal junctions of the MCOUPLED chain — precisely where the diagonal
chaining already joins the arm halves. Corner discontinuities and
cross-arm EM coupling of the fold are documented as unmodeled.

Validation: with bend length zero the hairpin must reproduce the
edge-coupled design of PR #51 exactly; with a real bend, the compensated
filter must stay centred in real qucsator (MCOUPLED + MLIN netlist)
while the *uncompensated* variant (90° sections plus bends) must centre
visibly lower — proving the compensation does its job, not just that
the response looks plausible.
"""

from __future__ import annotations

import shutil
import subprocess

import numpy as np
import pytest

from mcp_qucs_s.distributed import coupled_line_bpf, hairpin_bpf
from mcp_qucs_s.microstrip import Substrate
from mcp_qucs_s.netlist import generate_hairpin_netlist
from mcp_qucs_s.sparams import network_from_dat

HAS_QUCS = shutil.which("qucsator_rf") is not None or shutil.which("qucsator") is not None
requires_qucs = pytest.mark.skipif(not HAS_QUCS, reason="qucsator not installed")

F0 = 2.0e9
DELTA = 0.1
Z0 = 50.0
G_CHEB_05 = [1.0, 1.5963, 1.0967, 1.5963, 1.0]
ROGERS = Substrate(er=3.66, h_mm=0.508, t_um=35.0, tan_d=0.0037)


def _design(bend_mm=None):
    kwargs = {} if bend_mm is None else {"bend_mm": bend_mm}
    return hairpin_bpf(G_CHEB_05, F0, DELTA, z0=Z0, substrate=ROGERS, **kwargs)


# ---------------------------------------------------------------------------
# Fold bookkeeping
# ---------------------------------------------------------------------------


def test_zero_bend_degenerates_to_edge_coupled() -> None:
    hp = _design(bend_mm=0.0)
    ec = coupled_line_bpf(G_CHEB_05, F0, DELTA, z0=Z0, substrate=ROGERS)
    assert len(hp["sections"]) == len(ec["sections"])
    for h, e in zip(hp["sections"], ec["sections"], strict=True):
        assert h["z0e_ohm"] == pytest.approx(e["z0e_ohm"], rel=1e-12)
        assert h["width_mm"] == pytest.approx(e["width_mm"], rel=1e-12)
        assert h["gap_mm"] == pytest.approx(e["gap_mm"], rel=1e-12)
        assert h["length_mm"] == pytest.approx(e["length_mm"], rel=1e-12)
        assert h["electrical_length_deg"] == pytest.approx(90.0)


def test_resonators_total_half_wave_at_f0() -> None:
    d = _design()
    assert len(d["resonators"]) == len(G_CHEB_05) - 2, "order N ⇒ N hairpin resonators"
    for r in d["resonators"]:
        total = r["arm1_deg"] + r["bend_deg"] + r["arm2_deg"]
        assert total == pytest.approx(180.0, abs=1e-9), f"resonator {r['index']}: {total:.3f}°"


def test_sections_are_shortened_by_half_the_bend() -> None:
    d = _design()
    theta_b = d["resonators"][0]["bend_deg"]
    assert theta_b > 0.0
    for s in d["sections"]:
        assert s["electrical_length_deg"] == pytest.approx(90.0 - theta_b / 2.0, abs=1e-9)
        # physical length must shrink proportionally vs the unfolded λ/4
        quarter = s["length_mm"] / (s["electrical_length_deg"] / 90.0)
        assert s["length_mm"] < quarter


def test_default_bend_is_three_mean_widths() -> None:
    d = _design()
    mean_w = float(np.mean([s["width_mm"] for s in d["sections"]]))
    assert d["bend_mm"] == pytest.approx(3.0 * mean_w, rel=1e-9)


def test_bend_too_long_is_rejected() -> None:
    """A bend that eats the whole quarter-wave leaves nothing to couple."""
    with pytest.raises(ValueError, match="bend"):
        _design(bend_mm=60.0)


def test_hairpin_notes_name_the_unmodeled_effects() -> None:
    notes = " ".join(_design()["notes"]).lower()
    assert "corner" in notes or "bend discontinuit" in notes
    assert "cross" in notes or "self-coupling" in notes


# ---------------------------------------------------------------------------
# Netlist emission
# ---------------------------------------------------------------------------


def test_hairpin_netlist_places_bends_at_internal_junctions_only(tmp_path) -> None:
    d = _design(bend_mm=2.0)
    text = generate_hairpin_netlist(
        d, ROGERS, tmp_path / "hp.net", f_start_hz=1e9, f_stop_hz=3e9, points=101, sweep="lin"
    ).read_text()
    mcs = [ln for ln in text.splitlines() if ln.startswith("MCOUPLED:")]
    bends = [ln for ln in text.splitlines() if ln.startswith("MLIN:BEND")]
    assert len(mcs) == 4, "N=3 → 4 coupled sections"
    assert len(bends) == 3, "N=3 → 3 resonator bends, internal junctions only"
    assert all('L="2 mm"' in ln for ln in bends)
    # chain: section i line2-far → bend i → section i+1 line1-near
    for i in range(3):
        assert mcs[i].split()[3] == bends[i].split()[1]
        assert bends[i].split()[2] == mcs[i + 1].split()[1]
    # ports on the outer feed ends
    p1 = next(ln for ln in text.splitlines() if 'Num="1"' in ln).split()[1]
    p2 = next(ln for ln in text.splitlines() if 'Num="2"' in ln).split()[1]
    assert p1 == mcs[0].split()[1] and p2 == mcs[-1].split()[3]


# ---------------------------------------------------------------------------
# Against real qucsator
# ---------------------------------------------------------------------------


def _run(net, dat) -> None:
    exe = shutil.which("qucsator_rf") or shutil.which("qucsator")
    subprocess.run([exe, "-i", str(net), "-o", str(dat)], capture_output=True, timeout=120)
    assert dat.is_file()


def _peak_hz(tmp_path, design, name) -> tuple[float, float]:
    net = generate_hairpin_netlist(
        design,
        ROGERS,
        tmp_path / f"{name}.net",
        f_start_hz=1.2e9,
        f_stop_hz=2.8e9,
        points=1601,
        sweep="lin",
    )
    dat = tmp_path / f"{name}.dat"
    _run(net, dat)
    nw = network_from_dat(dat)
    s21 = 20.0 * np.log10(np.maximum(np.abs(nw.s[:, 1, 0]), 1e-12))
    i = int(np.argmax(s21))
    return float(nw.f[i]), float(s21[i])


@requires_qucs
@pytest.mark.qucs
@pytest.mark.integration
def test_compensated_hairpin_centers_near_design(tmp_path) -> None:
    d = _design(bend_mm=2.0)
    f_peak, peak_db = _peak_hz(tmp_path, d, "hp")
    assert abs(f_peak - F0) / F0 < 0.05, f"hairpin centred at {f_peak / 1e9:.3f} GHz"
    assert peak_db > -2.5, f"in-band loss {peak_db:.2f} dB"


@requires_qucs
@pytest.mark.qucs
@pytest.mark.integration
def test_compensation_beats_uncompensated(tmp_path) -> None:
    """Same bend, no section shortening: every resonator runs long, so the
    passband must sit visibly below the compensated design's centre."""
    d = _design(bend_mm=2.0)
    d_raw = _design(bend_mm=2.0)
    for s in d_raw["sections"]:
        s["length_mm"] = s["length_mm"] / (s["electrical_length_deg"] / 90.0)
        s["electrical_length_deg"] = 90.0
    f_comp, _ = _peak_hz(tmp_path, d, "comp")
    f_raw, _ = _peak_hz(tmp_path, d_raw, "raw")
    assert f_raw < f_comp, "longer resonators must resonate lower"
    assert abs(f_comp - F0) < abs(f_raw - F0), "compensation must improve centring"


# ---------------------------------------------------------------------------
# MCP tool envelope
# ---------------------------------------------------------------------------


def test_synthesize_hairpin_bpf_tool() -> None:
    from mcp_qucs_s import server

    env = server.synthesize_hairpin_bpf(
        g_coefficients=G_CHEB_05,
        f0_hz=F0,
        fractional_bandwidth=DELTA,
        substrate={"er": 3.66, "h_mm": 0.508, "tan_d": 0.0037},
    )
    assert env.status == "ok"
    assert env.data["n_sections"] == 4
    assert len(env.data["resonators"]) == 3
    total = env.data["resonators"][0]
    assert total["arm1_deg"] + total["bend_deg"] + total["arm2_deg"] == pytest.approx(180.0)


def test_synthesize_hairpin_bpf_tool_error_envelope() -> None:
    from mcp_qucs_s import server

    env = server.synthesize_hairpin_bpf(
        g_coefficients=[1.0, 1.5963],
        f0_hz=F0,
        fractional_bandwidth=DELTA,
        substrate={"er": 3.66, "h_mm": 0.508},
    )
    assert env.status == "error"
    assert "at least" in env.error
