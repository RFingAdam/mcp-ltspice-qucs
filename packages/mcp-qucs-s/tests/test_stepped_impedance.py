"""Stepped-impedance LPF synthesis (issue #27, first distributed topology).

Pozar §8.6: a lumped LPF prototype maps to alternating short sections of
very-high and very-low impedance line — series L → high-Z section with
``βl = ω_c·L/Z_h``, shunt C → low-Z section with ``βl = ω_c·C·Z_l``
(radians at the cutoff). The reference pin is Pozar 4e Example 8.6
(N=6 Butterworth, f_c = 2.5 GHz, Z_h = 120 Ω, Z_l = 20 Ω on εr = 4.2,
h = 1.58 mm): the six electrical lengths evaluate to 11.8634°, 33.7618°,
44.2745°, 46.1195°, 32.4102°, 12.3577° — hard-coded below as literals so
the test pins the implementation to the published formulas, not to
itself.

Simulator ground truth (established by probe netlists): qucsator's TLIN
is an ideal vacuum-velocity line (L = θ/360 · c/f_ref), so the ideal
netlist must match the analytical cascade to numerical precision; the
MLIN netlist exercises qucsator's *real* microstrip model (Hammerstad +
Kirschning dispersion) against our synthesized W/L.
"""

from __future__ import annotations

import math
import shutil
import subprocess

import numpy as np
import pytest

from mcp_qucs_s.distributed import stepped_impedance_lpf, tline_cascade_sparams
from mcp_qucs_s.microstrip import Substrate
from mcp_qucs_s.netlist import generate_ladder_netlist, generate_microstrip_ladder_netlist
from mcp_qucs_s.sparams import network_from_dat

HAS_QUCS = shutil.which("qucsator_rf") is not None or shutil.which("qucsator") is not None
requires_qucs = pytest.mark.skipif(not HAS_QUCS, reason="qucsator not installed")

FC = 2.5e9
Z0 = 50.0
Z_HIGH = 120.0
Z_LOW = 20.0
POZAR_SUBSTRATE = Substrate(er=4.2, h_mm=1.58, t_um=35.0, tan_d=0.02)

# Pozar 4e Example 8.6: N=6 Butterworth, shunt-first (C1 L2 C3 L4 C5 L6).
# g_k = 2·sin((2k−1)π/12) → 0.51764, 1.41421, 1.93185, 1.93185, 1.41421, 0.51764
_G6 = [2.0 * math.sin((2 * k - 1) * math.pi / 12.0) for k in range(1, 7)]
_WC = 2.0 * math.pi * FC
POZAR_COMPONENTS = {
    "C1": _G6[0] / (_WC * Z0),
    "L2": _G6[1] * Z0 / _WC,
    "C3": _G6[2] / (_WC * Z0),
    "L4": _G6[3] * Z0 / _WC,
    "C5": _G6[4] / (_WC * Z0),
    "L6": _G6[5] * Z0 / _WC,
}
# βl in degrees from Pozar (8.86a/b), evaluated by hand from the g-values
POZAR_BETA_L_DEG = [11.8634, 33.7618, 44.2745, 46.1195, 32.4102, 12.3577]


def _pozar_design():
    return stepped_impedance_lpf(
        POZAR_COMPONENTS,
        FC,
        z0=Z0,
        z_high=Z_HIGH,
        z_low=Z_LOW,
        substrate=POZAR_SUBSTRATE,
    )


# ---------------------------------------------------------------------------
# Closed-form synthesis math
# ---------------------------------------------------------------------------


def test_pozar_example_8_6_electrical_lengths() -> None:
    result = _pozar_design()
    sections = result["sections"]
    assert len(sections) == 6
    got = [s["electrical_length_deg"] for s in sections]
    assert got == pytest.approx(POZAR_BETA_L_DEG, abs=0.01)


def test_sections_alternate_low_and_high_z() -> None:
    sections = _pozar_design()["sections"]
    roles = [s["role"] for s in sections]
    assert roles == ["low_z", "high_z", "low_z", "high_z", "low_z", "high_z"]
    for s in sections:
        assert s["z0_ohm"] == (Z_LOW if s["role"] == "low_z" else Z_HIGH)


def test_widths_ordering_low_wider_than_high() -> None:
    """On any substrate: W(20 Ω) > W(50 Ω) > W(120 Ω)."""
    from mcp_qucs_s.microstrip import synthesize_width

    sections = _pozar_design()["sections"]
    w50 = synthesize_width(50.0, POZAR_SUBSTRATE)
    for s in sections:
        if s["role"] == "low_z":
            assert s["width_mm"] > w50
        else:
            assert s["width_mm"] < w50


def test_sections_beyond_45_degrees_are_flagged() -> None:
    """The 46.12° L4 section exceeds the βl < 45° short-line approximation
    bound; the synthesis must say so instead of silently proceeding."""
    result = _pozar_design()
    assert any("45" in n and "46.1" in n for n in result["notes"]), result["notes"]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"z_high": 40.0}, "z_high"),  # not above system Z0
        ({"z_low": 60.0}, "z_low"),  # not below system Z0
        ({"components": {}}, "no components"),
    ],
)
def test_invalid_inputs_rejected(kwargs, match) -> None:
    base: dict = {
        "components": POZAR_COMPONENTS,
        "cutoff_hz": FC,
        "z0": Z0,
        "z_high": Z_HIGH,
        "z_low": Z_LOW,
        "substrate": POZAR_SUBSTRATE,
    }
    base.update(kwargs)
    components = base.pop("components")
    cutoff_hz = base.pop("cutoff_hz")
    with pytest.raises(ValueError, match=match):
        stepped_impedance_lpf(components, cutoff_hz, **base)


# ---------------------------------------------------------------------------
# Analytical cascade response
# ---------------------------------------------------------------------------


def _analytic_s21_db(result, freqs):
    secs = [(s["z0_ohm"], s["electrical_length_deg"]) for s in result["sections"]]
    s = tline_cascade_sparams(secs, np.asarray(freqs, dtype=float), FC, z0_system=Z0)
    return 20.0 * np.log10(np.maximum(np.abs(s[:, 1, 0]), 1e-12))


def test_response_is_a_lowpass() -> None:
    result = _pozar_design()
    f = np.linspace(0.05e9, 5.0e9, 500)
    s21 = _analytic_s21_db(result, f)
    assert s21[0] > -0.3, "must pass near DC"
    # −3 dB crossing within 10% of the design cutoff (the short-line
    # approximation shifts it slightly; Pozar fig. 8.40 shows ~fc)
    crossing = f[int(np.argmin(np.abs(s21 + 3.0)))]
    assert abs(crossing - FC) / FC < 0.10, f"-3 dB at {crossing / 1e9:.2f} GHz"
    # stopband: Pozar's example is ~ -25 dB at 4 GHz for the stepped version
    at_4ghz = _analytic_s21_db(result, [4.0e9])[0]
    assert at_4ghz < -20.0, f"only {at_4ghz:.1f} dB at 4 GHz"


def test_cascade_is_reciprocal_and_passive() -> None:
    result = _pozar_design()
    f = np.linspace(0.1e9, 6.0e9, 200)
    secs = [(s["z0_ohm"], s["electrical_length_deg"]) for s in result["sections"]]
    s = tline_cascade_sparams(secs, f, FC, z0_system=Z0)
    assert np.allclose(s[:, 0, 1], s[:, 1, 0], rtol=1e-9)
    power = np.abs(s[:, 0, 0]) ** 2 + np.abs(s[:, 1, 0]) ** 2
    assert np.all(power < 1.0 + 1e-6), "lossless cascade cannot create power"
    assert np.all(power > 1.0 - 1e-6), "lossless cascade cannot absorb power"


# ---------------------------------------------------------------------------
# Netlist emission
# ---------------------------------------------------------------------------


def test_tline_element_uses_vacuum_length(tmp_path) -> None:
    """qucsator's TLIN propagates at c (probe-verified): 90° at 1 GHz must
    emit L = 74.948114 mm, and the element advances the signal node."""
    text = generate_ladder_netlist(
        [("series_tline", {"z0_ohm": 100.0, "theta_deg": 90.0, "f_ref_hz": 1e9})],
        tmp_path / "tl.net",
    ).read_text()
    tl = next(ln for ln in text.splitlines() if ln.startswith("TLIN:"))
    assert 'Z="100 Ohm"' in tl
    assert 'L="0.07494811' in tl  # metres, θ/360 · c/f_ref
    assert tl.split()[1] == "_p1"
    p2 = next(ln for ln in text.splitlines() if 'Num="2"' in ln).split()[1]
    assert p2 == tl.split()[2], "TLIN must advance the node to port 2"


def test_microstrip_netlist_emits_subst_once_and_mlin_chain(tmp_path) -> None:
    text = generate_microstrip_ladder_netlist(
        [
            {"width_mm": 11.0, "length_mm": 2.0},
            {"width_mm": 0.45, "length_mm": 5.0},
        ],
        POZAR_SUBSTRATE,
        tmp_path / "ms.net",
    ).read_text()
    subst_lines = [ln for ln in text.splitlines() if ln.startswith("SUBST:")]
    assert len(subst_lines) == 1
    assert 'er="4.2"' in subst_lines[0] and 'h="1.58 mm"' in subst_lines[0]
    mlins = [ln for ln in text.splitlines() if ln.startswith("MLIN:")]
    assert len(mlins) == 2
    assert 'W="11 mm"' in mlins[0] and 'L="2 mm"' in mlins[0]
    assert 'W="0.45 mm"' in mlins[1] and 'L="5 mm"' in mlins[1]
    assert all('Subst="Subst1"' in ln for ln in mlins)
    # chain: MLIN 1 output feeds MLIN 2 input; ports at the two ends
    assert mlins[0].split()[2] == mlins[1].split()[1]
    p1 = next(ln for ln in text.splitlines() if 'Num="1"' in ln).split()[1]
    p2 = next(ln for ln in text.splitlines() if 'Num="2"' in ln).split()[1]
    assert p1 == mlins[0].split()[1] and p2 == mlins[1].split()[2]


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
def test_ideal_tlin_matches_analytical_cascade(tmp_path) -> None:
    """Both are ideal dispersionless lines, so agreement must be at
    numerical precision — this validates the emitter length convention
    and the cascade math against a real solver simultaneously."""
    result = _pozar_design()
    elements = [
        (
            "series_tline",
            {"z0_ohm": s["z0_ohm"], "theta_deg": s["electrical_length_deg"], "f_ref_hz": FC},
        )
        for s in result["sections"]
    ]
    net = generate_ladder_netlist(
        elements, tmp_path / "si.net", f_start_hz=1e8, f_stop_hz=6e9, points=400, sweep="lin"
    )
    dat = tmp_path / "si.dat"
    _run_qucsator(net, dat)
    nw = network_from_dat(dat)
    qs21 = 20.0 * np.log10(np.maximum(np.abs(nw.s[:, 1, 0]), 1e-12))
    analytic = _analytic_s21_db(result, nw.f)
    mask = analytic > -90.0
    assert float(np.max(np.abs(qs21[mask] - analytic[mask]))) < 0.001


@requires_qucs
@pytest.mark.qucs
@pytest.mark.integration
def test_mlin_realization_cuts_off_near_design(tmp_path) -> None:
    """The synthesized W/L run through qucsator's *real* microstrip model
    (Hammerstad + Kirschning dispersion) — the −3 dB point must land near
    the design cutoff and the stopband must hold."""
    result = _pozar_design()
    net = generate_microstrip_ladder_netlist(
        [{"width_mm": s["width_mm"], "length_mm": s["length_mm"]} for s in result["sections"]],
        POZAR_SUBSTRATE,
        tmp_path / "ms.net",
        f_start_hz=1e8,
        f_stop_hz=6e9,
        points=400,
        sweep="lin",
    )
    dat = tmp_path / "ms.dat"
    _run_qucsator(net, dat)
    nw = network_from_dat(dat)
    s21 = 20.0 * np.log10(np.maximum(np.abs(nw.s[:, 1, 0]), 1e-12))
    crossing = nw.f[int(np.argmin(np.abs(s21 + 3.0)))]
    assert abs(crossing - FC) / FC < 0.10, f"-3 dB at {crossing / 1e9:.2f} GHz vs {FC / 1e9} design"
    at_4ghz = s21[int(np.argmin(np.abs(nw.f - 4.0e9)))]
    assert at_4ghz < -15.0, f"microstrip stopband only {at_4ghz:.1f} dB at 4 GHz"
