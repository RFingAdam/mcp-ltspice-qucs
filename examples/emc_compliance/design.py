#!/usr/bin/env python3
"""Pre-compliance check: predict CISPR 22 conducted + FCC 15.109 radiated.

Runs the EMC tools against a sanitised SMPS profile (assumed harmonic
spectrum) and a clock-loop radiator, comparing predicted vs limits.
Useful early in design to spot likely fail conditions before hitting
the EMC chamber.
"""

from __future__ import annotations

from pathlib import Path

from mcp_rf_analysis.emc import (
    cispr_limit_at,
    fcc_part15_radiated_limit_at,
    predict_conducted_emissions,
    predict_radiated_emissions_loop,
)

HERE = Path(__file__).parent


def main() -> None:
    # ---- 1. Conducted: assumed SMPS line-current harmonic spectrum -----
    # Switching at 1 MHz, 100 mA fundamental, harmonics rolling off
    # ~6 dB/octave. Realistic for a moderately filtered buck converter.
    fundamental_ma = 100.0
    spectrum = []
    for n in range(1, 30):
        f = n * 1e6
        if f < 150e3 or f > 30e6:
            continue
        # 1/n attenuation per harmonic (roughly -6 dB/oct for triangular wave)
        i_mA = fundamental_ma / n
        spectrum.append((f, i_mA / 1000))  # convert mA → A
    print(
        f"Predicting conducted emissions for {len(spectrum)} harmonics, "
        f"fundamental {fundamental_ma:.0f} mA"
    )
    res_cond = predict_conducted_emissions(
        spectrum,
        standard="cispr22_b",
        margin_db=6.0,
    )
    print("\nConducted vs CISPR 22 Class B (with 6 dB design margin):")
    print(
        f"  Overall: {res_cond['overall'].upper()}, "
        f"{res_cond['n_violations']}/{len(spectrum)} freq points fail"
    )
    print(f"\n{'Freq (MHz)':>11} {'Measured (dBµV)':>17} {'Limit':>8} {'Margin':>8}  Status")
    for f, m, lim, marg, status in zip(
        res_cond["freq_hz"],
        res_cond["measured_dbuv"],
        res_cond["limit_dbuv"],
        res_cond["margin_db_per_freq"],
        res_cond["status"],
        strict=True,
    ):
        flag = "PASS" if status == "pass" else "FAIL"
        print(f"  {f / 1e6:>9.2f}    {m:>14.1f}    {lim:>5.1f}    {marg:+6.1f}    {flag}")

    # ---- 2. Radiated: clock loop -------------------------------------
    # 100 MHz clock, 2 mA harmonic content in a 5 cm² loop on the PCB.
    # FCC §15.109 measurement at 3 m.
    print("\n\nPredicting radiated emissions from a clock loop:")
    print("  100 MHz harmonic, 2 mA loop current, 5 cm² loop area, 3 m distance")
    print(
        f"\n{'Harmonic':>9} {'Freq (MHz)':>11} {'Predicted (dBµV/m)':>20} {'Limit':>8} {'Margin':>8}"
    )
    base_current_mA = 2.0
    loop_area_cm2 = 5.0
    n_violations = 0
    for n in range(1, 11):
        f_h = n * 100e6
        if f_h > 1e9:
            continue
        # Harmonics fall ~1/n
        i_a = (base_current_mA / n) * 1e-3
        rad = predict_radiated_emissions_loop(
            current_a=i_a,
            loop_area_cm2=loop_area_cm2,
            freq_hz=f_h,
            measurement_distance_m=3.0,
        )
        limit = fcc_part15_radiated_limit_at(f_h, distance_m=3.0)
        margin = limit - rad["e_dbuv_per_m"]
        flag = "PASS" if margin >= 0 else "FAIL"
        print(
            f"  {n:>7}H {f_h / 1e6:>9.0f}  {rad['e_dbuv_per_m']:>17.1f}  "
            f"{limit:>6.1f}  {margin:+6.1f}  {flag}"
        )
        if margin < 0:
            n_violations += 1

    print(f"\n  {n_violations} radiated violations under FCC §15.109 Class B")

    # ---- 3. Quick limit lookups for spec sheets ----------------------
    print("\n\nReference limits (no measurement, just look up):")
    for f_hz in (150e3, 1e6, 10e6, 30e6):
        print(f"  CISPR 22 Class B QP @ {f_hz / 1e6:>5.2f} MHz: {cispr_limit_at(f_hz):.1f} dBµV")
    for f_hz in (40e6, 100e6, 250e6, 800e6, 2.4e9):
        print(
            f"  FCC §15.109 Class B @ {f_hz / 1e6:>6.0f} MHz, 3m: "
            f"{fcc_part15_radiated_limit_at(f_hz):.1f} dBµV/m"
        )

    print("\n  See report.md for the structured tables.")
    _write_report(HERE / "report.md", res_cond)


def _write_report(path, res_cond) -> None:
    lines = [
        "# EMC pre-compliance check",
        "",
        "First-order conducted + radiated emissions estimate using the",
        "`mcp-rf-analysis` EMC tools.",
        "",
        "## Conducted emissions vs CISPR 22 Class B (with 6 dB margin)",
        "",
        f"**Overall: {res_cond['overall'].upper()}** "
        f"({res_cond['n_violations']} of {len(res_cond['freq_hz'])} points fail)",
        "",
        "| Freq (MHz) | Measured (dBµV) | Limit (dBµV) | Margin (dB) | Status |",
        "|---|---|---|---|---|",
    ]
    for f, m, lim, marg, status in zip(
        res_cond["freq_hz"],
        res_cond["measured_dbuv"],
        res_cond["limit_dbuv"],
        res_cond["margin_db_per_freq"],
        res_cond["status"],
        strict=True,
    ):
        flag = "✅ PASS" if status == "pass" else "❌ FAIL"
        lines.append(f"| {f / 1e6:.2f} | {m:.1f} | {lim:.1f} | {marg:+.1f} | {flag} |")
    lines.append("")
    lines.append("> Conducted emissions assume a 100 mA SMPS fundamental at 1 MHz")
    lines.append("> with 1/n harmonic rolloff. Replace `spectrum` in design.py")
    lines.append("> with your real switching-current spectrum from LTspice.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
