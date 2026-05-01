"""Build investor-facing charts from the v0.2 MCP suite.

Runs three demos end-to-end (no fakery) and renders polished PNGs.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from mcp_ltspice.analog.cascade import cascaded_lpf_design, transfer_function_db
from mcp_ltspice.eval import FilterSpec
from mcp_ltspice.vendors.opamps import find_opamp_for_application
from mcp_ltspice.extract import (
    components_dict_to_elements,
    ladder_sparams_from_components,
)
from mcp_ltspice.montecarlo import monte_carlo_analysis
from mcp_ltspice.power.emc import predict_conducted_emissions
from mcp_ltspice.synthesis import synthesize_lc_lpf

ASSETS = Path(__file__).parent / "assets"
ASSETS.mkdir(exist_ok=True, parents=True)

# Anthropic-leaning palette
NAVY = "#1f2a44"
TEAL = "#1a8a8a"
ORANGE = "#e7754a"
RED = "#c83737"
GRAY = "#6b7280"

plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.titleweight": "bold",
    }
)


def _s21_db(els, freqs):
    s = ladder_sparams_from_components(els, freqs, z0=50.0)
    return 20 * np.log10(np.abs(s[:, 1, 0]))


def chart_lpf() -> dict:
    """Demo 1: 1 GHz Butterworth LPF — synthesise, analyse, run MC yield.

    Spec mirrors examples/basic_lpf/spec.json so the demo numbers match
    the public reference example.
    """
    fc_hz = 1e9
    design = synthesize_lc_lpf("butterworth", 5, fc_hz)
    els = components_dict_to_elements(
        design.components, topology=design.topology, kind="lowpass"
    )
    f = np.geomspace(10e6, 20e9, 1001)
    s21 = _s21_db(els, f)

    spec = FilterSpec.model_validate(
        {
            "passband": {
                "f_start": 1e6,
                "f_stop": 600e6,
                "il_max_db": 0.5,
                "rl_min_db": 14.0,
            },
            "stopband_targets": [
                {"freq": 2e9, "rejection_min_db": 30.0, "label": "2x fc"},
                {"freq": 3e9, "rejection_min_db": 45.0, "label": "3x fc"},
                {"freq": 5e9, "rejection_min_db": 60.0, "label": "5x fc"},
            ],
        }
    )
    mc = monte_carlo_analysis(
        design.components,
        spec,
        tolerance_pct=5.0,
        n_runs=500,
        n_jobs=-1,
        base_seed=42,
    )

    fig, (ax_r, ax_y) = plt.subplots(1, 2, figsize=(11, 4.0), constrained_layout=True)

    ax_r.semilogx(f / 1e9, s21, color=NAVY, lw=2, label="|S21| nominal")
    ax_r.axvline(1.0, color=GRAY, ls=":", lw=1)
    ax_r.axvline(2.0, color=GRAY, ls=":", lw=1)
    ax_r.axvline(3.0, color=GRAY, ls=":", lw=1)
    ax_r.scatter([2.0, 3.0, 5.0], [-30, -45, -60], color=ORANGE, marker="v", s=70,
                 zorder=5, label="Stopband targets")
    ax_r.text(0.04, -3, "Passband: IL ≤ 0.5 dB,\nRL ≥ 14 dB to 600 MHz",
              color=TEAL, fontsize=9)
    ax_r.text(2.1, -28, "30 dB @ 2·fc", color=ORANGE, fontsize=9)
    ax_r.text(3.1, -43, "45 dB @ 3·fc", color=ORANGE, fontsize=9)
    ax_r.text(5.1, -58, "60 dB @ 5·fc", color=ORANGE, fontsize=9)
    ax_r.set_xlabel("Frequency (GHz)")
    ax_r.set_ylabel("|S21| (dB)")
    ax_r.set_title("1 GHz LPF — synthesise + analyse + verify in seconds")
    ax_r.set_ylim(-80, 5)
    ax_r.set_xlim(0.01, 20)
    ax_r.grid(True, which="both", alpha=0.25)
    ax_r.legend(loc="lower left", fontsize=8, framealpha=0.9)

    yield_pct = mc.yield_pct
    ax_y.bar([0], [yield_pct], color=TEAL, width=0.6)
    ax_y.bar([1], [100 - yield_pct], color=RED, width=0.6)
    ax_y.set_xticks([0, 1])
    ax_y.set_xticklabels(["Pass", "Fail"])
    ax_y.set_ylabel("Trials (%)")
    ax_y.set_ylim(0, 105)
    ax_y.set_title(f"Monte Carlo yield, ±5 % tolerance, {mc.n_runs} trials")
    ax_y.text(0, yield_pct + 2, f"{yield_pct:.1f} %", ha="center", fontweight="bold")
    if 100 - yield_pct > 1:
        ax_y.text(1, 100 - yield_pct + 2, f"{100 - yield_pct:.1f} %", ha="center")

    fig.savefig(ASSETS / "demo1_lpf.png", dpi=150)
    plt.close(fig)

    return {
        "components": design.components,
        "yield_pct": yield_pct,
        "n_runs": mc.n_runs,
    }


def chart_smps_emc() -> dict:
    """Demo 2: 24 V buck conducted-emissions vs CISPR 32 Class B (QP)."""
    no_filter = predict_conducted_emissions(
        f_switching_hz=200e3,
        duty_cycle=0.30,
        switch_voltage_v=24.0,
        rise_time_s=20e-9,
        n_harmonics=80,
        filter_attenuation_db_at_f_sw=0.0,
        cispr_class="class_b",
        cispr_detector="qp",
    )
    with_filter = predict_conducted_emissions(
        f_switching_hz=200e3,
        duty_cycle=0.30,
        switch_voltage_v=24.0,
        rise_time_s=20e-9,
        n_harmonics=80,
        filter_attenuation_db_at_f_sw=70.0,
        filter_attenuation_slope_db_per_decade=40.0,
        cispr_class="class_b",
        cispr_detector="qp",
    )

    fig, ax = plt.subplots(figsize=(11, 4.5), constrained_layout=True)
    f_mhz = np.array(no_filter.freq_hz) / 1e6
    e_no = np.array(no_filter.emission_dbuv, dtype=float)
    e_yes = np.array(with_filter.emission_dbuv, dtype=float)
    lim = np.array(with_filter.limit_dbuv, dtype=float)
    # Clip sinc-zero dips so they don't dominate the y-axis
    e_no = np.where(e_no < -20, np.nan, e_no)
    e_yes = np.where(e_yes < -20, np.nan, e_yes)

    ax.semilogx(f_mhz, e_no, color=RED, lw=1.5, marker="o", ms=3, label="No filter")
    ax.semilogx(
        f_mhz, e_yes, color=TEAL, lw=1.5, marker="o", ms=3,
        label="With designed LC input filter (70 dB @ fsw)",
    )
    ax.semilogx(f_mhz, lim, color=NAVY, lw=2, ls="--",
                label="CISPR 32 Class B (QP) limit")
    ax.fill_between(f_mhz, lim, lim + 80, color=RED, alpha=0.05)
    ax.text(0.18, 130, "Violation zone", color=RED, fontsize=9, alpha=0.8)
    ax.set_xlabel("Frequency (MHz)")
    ax.set_ylabel("Conducted emission (dBµV)")
    ax.set_title(
        "200 kHz buck — predict, design filter, verify against CISPR 32 Class B",
        fontsize=11,
    )
    ax.legend(loc="upper right", framealpha=0.95)
    ax.grid(True, which="both", alpha=0.25)
    ax.set_xlim(0.15, 30)
    ax.set_ylim(-20, 160)

    fig.savefig(ASSETS / "demo2_emc.png", dpi=150)
    plt.close(fig)

    return {
        "worst_no_filter_db": float(no_filter.worst_margin_db),
        "worst_with_filter_db": float(with_filter.worst_margin_db),
        "filter_pass": with_filter.pass_status,
    }


def chart_sallen_key() -> dict:
    """Demo 3: 22 kHz active LPF — Sallen-Key, 4th order, op-amp recommendation."""
    fc_hz = 22e3
    design = cascaded_lpf_design(fc_hz=fc_hz, order=4, response="butterworth")
    tf = transfer_function_db(fc_hz=fc_hz, order=4, response="butterworth")
    f = np.asarray(tf["freq_hz"])
    db = np.asarray(tf["h_db"])

    # 22 kHz audio anti-aliasing: prefer low-noise audio precision parts,
    # not the highest-GBW amp in the catalogue. Cap GBW to keep the search
    # in audio-appropriate territory and rank the remainder by lowest noise.
    candidates = find_opamp_for_application(
        min_gbw_mhz=design["op_amp_min_gbw_hz"] / 1e6,
        max_input_noise_nv_per_rthz=10.0,
        max_input_offset_uv=1000.0,
    )
    audio_candidates = [c for c in candidates if c.gbw_mhz <= 200.0]
    audio_candidates.sort(key=lambda c: c.input_noise_nv_per_rthz)
    chosen = audio_candidates[0] if audio_candidates else (candidates[0] if candidates else None)

    fig, ax = plt.subplots(figsize=(11, 4.5), constrained_layout=True)
    ax.semilogx(f, db, color=NAVY, lw=2)
    ax.axvline(22e3, color=GRAY, ls=":", lw=1)
    ax.axhline(-3, color=GRAY, ls=":", lw=1)
    ax.text(
        22.5e3,
        -3.5,
        "fc = 22 kHz, −3 dB",
        fontsize=9,
        color=NAVY,
    )

    if chosen is not None:
        ax.text(
            0.12,
            0.04,
            f"Recommended op-amp: {chosen.part_number} ({chosen.vendor})\n"
            f"  GBW={chosen.gbw_mhz:.0f} MHz · noise={chosen.input_noise_nv_per_rthz:.1f} nV/√Hz\n"
            f"  → {len(candidates)} candidates auto-screened from bundled catalog",
            transform=ax.transAxes,
            fontsize=9,
            family="monospace",
            bbox={
                "facecolor": "white",
                "edgecolor": GRAY,
                "boxstyle": "round,pad=0.5",
            },
        )

    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("|H(f)| (dB)")
    ax.set_title(
        "22 kHz, 4th-order Butterworth (cascaded Sallen-Key) + op-amp recommendation",
        fontsize=11,
    )
    ax.grid(True, which="both", alpha=0.25)
    ax.set_ylim(-80, 5)
    fig.savefig(ASSETS / "demo3_sallen_key.png", dpi=150)
    plt.close(fig)

    return {
        "stages": design["n_stages"],
        "recommended_part": chosen.part_number if chosen else None,
    }


def chart_hero() -> None:
    """Headline graphic — capability fan-out."""
    fig = plt.figure(figsize=(13, 6.0), constrained_layout=True)
    gs = fig.add_gridspec(2, 3, height_ratios=[1, 1.4])

    ax_title = fig.add_subplot(gs[0, :])
    ax_title.axis("off")
    ax_title.text(
        0.5,
        0.75,
        "mcp-ltspice-qucs",
        ha="center",
        va="center",
        fontsize=30,
        fontweight="bold",
        color=NAVY,
    )
    ax_title.text(
        0.5,
        0.30,
        "An agentic MCP suite for RF, analog, and SMPS-EMC design — "
        "spec to compliant deliverable in seconds",
        ha="center",
        va="center",
        fontsize=13,
        color=GRAY,
        style="italic",
    )

    panels = [
        (
            "RF / Microwave",
            "LC ladder synthesis\n"
            "(Butterworth · Chebyshev · Elliptic · HPF/BPF/BSF)\n"
            "vendor part substitution\n"
            "Monte Carlo yield\n"
            "microstrip + 16 substrates",
            TEAL,
        ),
        (
            "Power-supply EMC",
            "Pi LC output filter\n"
            "DM input filter (Middlebrook stability)\n"
            "Conducted-emissions prediction\n"
            "CISPR 22/32 limit overlay\n"
            "snubber · CM choke",
            ORANGE,
        ),
        (
            "Analog + Mixed-signal",
            "Sallen-Key / MFB synthesis\n"
            "Buck · Boost · LDO sizing\n"
            "Type-II compensator\n"
            "Setup/hold timing\n"
            "Op-amp / MOSFET / BJT catalogs",
            NAVY,
        ),
    ]
    for col, (title, body, color) in enumerate(panels):
        ax = fig.add_subplot(gs[1, col])
        ax.axis("off")
        ax.add_patch(
            plt.Rectangle(
                (0.02, 0.02),
                0.96,
                0.96,
                transform=ax.transAxes,
                facecolor=color,
                alpha=0.10,
                edgecolor=color,
                linewidth=2,
            )
        )
        ax.text(
            0.5,
            0.88,
            title,
            ha="center",
            va="center",
            fontsize=14,
            fontweight="bold",
            color=color,
            transform=ax.transAxes,
        )
        ax.text(
            0.5,
            0.42,
            body,
            ha="center",
            va="center",
            fontsize=10.5,
            color=NAVY,
            transform=ax.transAxes,
        )

    fig.savefig(ASSETS / "hero.png", dpi=150)
    plt.close(fig)


def main() -> None:
    print("Building hero graphic...")
    chart_hero()
    print("Demo 1: RF LPF...")
    info1 = chart_lpf()
    print("  ", info1)
    print("Demo 2: SMPS EMC...")
    info2 = chart_smps_emc()
    print("  ", info2)
    print("Demo 3: Sallen-Key...")
    info3 = chart_sallen_key()
    print("  ", info3)
    print(f"All charts written to {ASSETS}")


if __name__ == "__main__":
    main()
