#!/usr/bin/env python3
"""5V → 3.3V buck SMPS at 2A — component sizing + MOSFET selection.

Demonstrates the power-supply tools: design_buck for L/Cout sizing,
find_mosfet_for_application for switch selection, type2_compensator
for the control loop.
"""

from __future__ import annotations

from pathlib import Path

from mcp_ltspice.power import design_buck, type2_compensator
from mcp_ltspice.vendors import find_mosfet_for_application

HERE = Path(__file__).parent

V_IN = 5.0
V_OUT = 3.3
I_OUT = 2.0
F_SW = 1_000_000  # 1 MHz


def main() -> None:
    print(f"Buck SMPS: {V_IN}V → {V_OUT}V at {I_OUT}A, fsw = {F_SW / 1e6:.1f} MHz")

    # 1. Size the power stage
    d = design_buck(
        v_in_v=V_IN,
        v_out_v=V_OUT,
        i_out_a=I_OUT,
        f_sw_hz=F_SW,
        inductor_ripple_pct=30,
        output_ripple_mvpp=20,
    )
    print("\n[1] Power-stage sizing:")
    print(f"    Duty cycle:      {d.duty_cycle * 100:.1f}%")
    print(
        f"    Inductor:        {d.L_h * 1e6:.2f} µH, peak {d.inductor_peak_a:.2f} A, RMS {d.inductor_rms_a:.2f} A"
    )
    print(f"    Output cap:      {d.Cout_f * 1e6:.1f} µF, ESR ≤ {d.Cout_esr_max_ohm * 1000:.1f} mΩ")
    if d.notes:
        print(f"    Notes: {'; '.join(d.notes)}")

    # 2. Pick a high-side MOSFET — needs to handle V_in plus margin, plus the
    #    peak inductor current with margin, low Rds_on for efficiency
    print(f"\n[2] High-side MOSFET candidates (Vds ≥ 10 V, Id ≥ {d.inductor_peak_a * 1.5:.1f} A):")
    candidates = find_mosfet_for_application(
        polarity="N",
        min_vds_v=10,  # 2× V_in margin
        min_id_a=d.inductor_peak_a * 1.5,  # 50% margin over peak
        max_vgs_threshold_v=2.0,  # logic-level so 5V gate works
        sort_by="rds_on_max_mohm",
    )
    for m in candidates[:5]:
        print(
            f"    {m.part_number:<14} ({m.vendor:<14}) "
            f"Vds={m.vds_max_v:>4.0f} V, Id={m.id_continuous_a:>5.1f} A, "
            f"Rds_on={m.rds_on_max_mohm:>5.1f} mΩ, Qg={m.qg_total_nc:>4.1f} nC"
        )
    chosen = candidates[0] if candidates else None
    if chosen:
        # Estimate switching + conduction losses.
        #   P_cond = I_rms² · Rds_on · D
        #   P_sw   = 0.5 · V_in · I_peak · t_transition · f_sw
        # t_transition is the Vds rise+fall time; assume 5 ns from a
        # typical gate driver. For more accuracy, scale this by Qgd /
        # I_gate_drive — but for the first-pass estimate 5 ns is fine.
        t_transition_ns = 5.0
        p_cond = (d.inductor_rms_a**2) * (chosen.rds_on_max_mohm * 1e-3) * d.duty_cycle
        p_sw = 0.5 * V_IN * d.inductor_peak_a * (t_transition_ns * 1e-9) * F_SW
        p_total = p_cond + p_sw
        print(f"\n    Chosen: {chosen.part_number}")
        print(f"      Conduction loss: {p_cond * 1000:.0f} mW")
        print(f"      Switching loss:  {p_sw * 1000:.0f} mW (5 ns Vds transition)")
        print(f"      Total switch loss: {p_total * 1000:.0f} mW")
        eta = (V_OUT * I_OUT) / (V_OUT * I_OUT + p_total) * 100
        print(f"      Estimated efficiency: {eta:.1f}%")

    # 3. Loop compensator (current-mode → Type-II)
    #    Plant: single dominant pole at fp = 1/(2π·R_load·Cout) and
    #    output-cap ESR zero at fz = 1/(2π·ESR·Cout).
    print("\n[3] Control-loop compensator (Type-II, current-mode):")
    r_load = V_OUT / I_OUT
    f_pole = 1 / (2 * 3.14159 * r_load * d.Cout_f)
    f_zero = 1 / (2 * 3.14159 * (d.Cout_esr_max_ohm / 2) * d.Cout_f)  # ESR ≈ half max
    f_xover = F_SW / 10  # 1/10 of fsw
    comp = type2_compensator(
        crossover_hz=f_xover,
        plant_zero_hz=f_zero,
        plant_pole_hz=f_pole,
        phase_boost_deg=60.0,
    )
    print(f"    Crossover target: {f_xover / 1e3:.1f} kHz")
    print(f"    Plant pole (Rload·Cout): {f_pole:.0f} Hz")
    print(f"    ESR zero (Cout·ESR):     {f_zero / 1e3:.1f} kHz")
    print("    Compensator components:")
    for k, v in comp.components.items():
        if k.startswith("R"):
            print(f"      {k}: {v / 1e3:.2f} kΩ")
        else:
            print(f"      {k}: {v * 1e9:.1f} nF")

    # 4. Write report
    _write_report(HERE / "report.md", d, chosen, comp, f_pole, f_zero, f_xover)
    print("\n  Wrote: report.md")


def _write_report(path, d, chosen, comp, f_pole, f_zero, f_xover) -> None:
    lines = [
        f"# Buck SMPS: {V_IN}V → {V_OUT}V at {I_OUT}A",
        "",
        f"Switching frequency: **{F_SW / 1e6:.1f} MHz**",
        "",
        "## Power-stage components",
        "",
        f"- Duty cycle: **{d.duty_cycle * 100:.1f}%**",
        f"- Inductor: **{d.L_h * 1e6:.2f} µH** (peak {d.inductor_peak_a:.2f} A, RMS {d.inductor_rms_a:.2f} A)",
        f"- Output capacitance: **{d.Cout_f * 1e6:.1f} µF**",
        f"- Output cap ESR limit: **{d.Cout_esr_max_ohm * 1000:.1f} mΩ**",
        "",
    ]
    if chosen:
        lines += [
            "## High-side switch",
            "",
            f"**{chosen.part_number}** ({chosen.vendor})",
            "",
            f"- Vds max: {chosen.vds_max_v} V",
            f"- Id continuous: {chosen.id_continuous_a} A",
            f"- Rds_on: {chosen.rds_on_max_mohm} mΩ",
            f"- Qg total: {chosen.qg_total_nc} nC",
            f"- Vgs threshold: {chosen.vgs_threshold_v} V",
            f"- Package: {chosen.package}",
            "",
        ]
    lines += [
        "## Loop compensator (Type-II)",
        "",
        f"- Crossover: **{f_xover / 1e3:.1f} kHz** (≈ fsw/10)",
        f"- Plant pole: {f_pole:.0f} Hz",
        f"- ESR zero: {f_zero / 1e3:.1f} kHz",
        "",
        "Compensator R/C:",
        "",
        "| Component | Value |",
        "|---|---|",
    ]
    for k, v in comp.components.items():
        unit = "kΩ" if k.startswith("R") else "nF"
        scaled = v / 1e3 if k.startswith("R") else v * 1e9
        lines.append(f"| {k} | {scaled:.2f} {unit} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
