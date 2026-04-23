#!/usr/bin/env python3
"""Anti-aliasing op-amp LPF in front of a 24-bit audio ADC.

Designs a 4th-order Butterworth low-pass filter with corner at 22 kHz
(just above the audio band) to suppress aliasing for a 96 kSPS ADC.
Uses two cascaded Sallen-Key 2nd-order stages, each with its own
characteristic Q from the standard Butterworth table. Picks an op-amp
from the bundled vendor catalog that meets GBW + noise + offset.

Generates:
  - report.md       : per-stage component values + op-amp choice rationale
  - response.png    : transfer function magnitude (analytical)
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from mcp_ltspice.analog import cascaded_lpf_design
from mcp_ltspice.analog.cascade import transfer_function_db
from mcp_ltspice.schematic_render import render_cascaded_lpf_schematic
from mcp_ltspice.vendors import find_opamp_for_application

HERE = Path(__file__).parent

FC_HZ = 22_000.0  # corner above audio (20 kHz)
ORDER = 4  # 4th-order = 24 dB/oct stopband
RIPPLE_DB = 0.0  # Butterworth (max-flat passband)


def main() -> None:
    print(f"Designing {ORDER}th-order Butterworth LPF at fc = {FC_HZ / 1e3:.1f} kHz")
    print("  Topology: cascaded Sallen-Key (2 stages of 2nd-order)")

    # 1. Synthesize per-stage component values
    design = cascaded_lpf_design(fc_hz=FC_HZ, order=ORDER, response="butterworth")
    print(f"\n  {design['n_stages']} stages, requires {design['n_op_amps_required']} op-amps")
    print(f"  Op-amp GBW must exceed {design['op_amp_min_gbw_hz'] / 1e6:.2f} MHz\n")

    for stage in design["stages"]:
        if stage["components"] is None:
            continue
        c = stage["components"]
        print(
            f"  Stage {stage['stage_index']}: fc={stage['fc_hz'] / 1e3:.2f} kHz, Q={stage['q']:.3f}"
        )
        print(f"    R1 = {c['R1']:.0f} Ω, R2 = {c['R2']:.0f} Ω")
        print(f"    C1 = {c['C1'] * 1e9:.1f} nF, C2 = {c['C2'] * 1e9:.1f} nF")

    # 2. Pick an op-amp: low-noise audio precision, RRIO not strictly required
    #    for ±15V supply but nice to have for ±5V or single-supply systems
    candidates = find_opamp_for_application(
        min_gbw_mhz=design["op_amp_min_gbw_hz"] / 1e6,
        max_input_noise_nv_per_rthz=10.0,  # < 10 nV/√Hz for clean audio
        max_input_offset_uv=1000.0,  # ≤ 1 mV offset (don't bias DC into ADC)
    )
    print(f"\n  {len(candidates)} op-amp candidates from the bundled catalog:")
    for c in candidates[:5]:
        print(
            f"    {c.part_number:<10} ({c.vendor:<10}) GBW={c.gbw_mhz:>5.1f} MHz, "
            f"noise={c.input_noise_nv_per_rthz:>4.1f} nV/√Hz, "
            f"offset={c.input_offset_max_uv:>5.0f} µV → {c.typical_use}"
        )
    chosen = candidates[0] if candidates else None
    if chosen:
        print(f"\n  Recommended: {chosen.part_number} ({chosen.vendor})")

    # 3. Plot the analytical transfer function
    tf = transfer_function_db(FC_HZ, ORDER, response="butterworth")
    f = np.asarray(tf["freq_hz"])
    h = np.asarray(tf["h_db"])
    fig, ax = plt.subplots(figsize=(10, 6), dpi=120)
    ax.semilogx(f, h, "C0", lw=2, label="|H| (4th-order Butterworth)")
    ax.axvline(FC_HZ, color="black", ls=":", alpha=0.5, label=f"fc = {FC_HZ / 1e3:.0f} kHz")
    ax.axvline(48_000, color="red", ls=":", alpha=0.5, label="Nyquist (96 kSPS) = 48 kHz")
    ax.axhline(-3, color="gray", ls=":", alpha=0.3)
    ax.axhline(0, color="gray", ls=":", alpha=0.3)
    ax.set_xlabel("Frequency [Hz]")
    ax.set_ylabel("|H| [dB]")
    ax.set_title("Anti-aliasing LPF for 96 kSPS audio ADC")
    ax.set_ylim(-100, 5)
    ax.set_xlim(100, 1e6)
    ax.legend()
    ax.grid(True, which="both", alpha=0.3)
    fig.tight_layout()
    out = HERE / "response.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"\n  Wrote: {out.name}")

    # Find rejection at Nyquist
    rejection_at_nyquist = float(np.interp(48_000, f, h))
    print(f"  Rejection at Nyquist (48 kHz): {rejection_at_nyquist:.1f} dB")

    # 4. Render publication-quality Sallen-Key schematic for each stage
    print("\n  Rendering per-stage schematics...")
    schematic_paths = render_cascaded_lpf_schematic(
        design,
        output_dir=HERE,
        base_name="schematic_stage",
    )
    for p in schematic_paths:
        print(f"  Wrote: {p.name}")

    # 5. Write report
    _write_report(HERE / "report.md", design, chosen, rejection_at_nyquist)
    print("  Wrote: report.md")


def _write_report(path, design, chosen, rejection_at_nyquist) -> None:
    lines = [
        "# Anti-aliasing LPF for 96 kSPS audio ADC",
        "",
        "4th-order Butterworth low-pass filter, fc = 22 kHz, cascaded Sallen-Key.",
        "",
        f"- **Order:** {design['order']}",
        f"- **Topology:** {design['topology']}",
        f"- **Stages:** {design['n_stages']} ({design['n_op_amps_required']} op-amps required)",
        f"- **Min op-amp GBW:** {design['op_amp_min_gbw_hz'] / 1e6:.2f} MHz",
        f"- **Stopband rejection at Nyquist (48 kHz):** {rejection_at_nyquist:.1f} dB",
        "",
        "## Per-stage components",
        "",
        "| Stage | fc | Q | R1 | R2 | C1 | C2 |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in design["stages"]:
        if s["components"] is None:
            continue
        c = s["components"]
        lines.append(
            f"| {s['stage_index']} | {s['fc_hz'] / 1e3:.2f} kHz | {s['q']:.3f} | "
            f"{c['R1']:.0f} Ω | {c['R2']:.0f} Ω | "
            f"{c['C1'] * 1e9:.1f} nF | {c['C2'] * 1e9:.1f} nF |"
        )
    lines.append("")
    if chosen is not None:
        lines.append("## Op-amp choice")
        lines.append("")
        lines.append(f"**{chosen.part_number}** ({chosen.vendor}) — {chosen.typical_use}")
        lines.append("")
        lines.append(f"- Family: {chosen.family}")
        lines.append(
            f"- GBW: {chosen.gbw_mhz:.1f} MHz (need > {design['op_amp_min_gbw_hz'] / 1e6:.1f})"
        )
        lines.append(f"- Slew rate: {chosen.slew_rate_v_per_us:.1f} V/µs")
        lines.append(f"- Input noise: {chosen.input_noise_nv_per_rthz:.1f} nV/√Hz")
        lines.append(f"- Input offset: {chosen.input_offset_max_uv:.0f} µV")
        lines.append(f"- Supply: {chosen.supply_min_v}-{chosen.supply_max_v} V")
        lines.append(f"- RRIO: in={chosen.rail_to_rail_input}, out={chosen.rail_to_rail_output}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
