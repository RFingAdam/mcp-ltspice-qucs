"""Buck (step-down) SMPS component sizing.

First-order analytical design — picks L and Cout from ripple targets,
duty cycle from V_in/V_out ratio, ESR target from output ripple budget.
This is what you'd hand-calculate before opening a vendor's design tool;
it gives you the right ballpark without a spreadsheet.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class BuckDesign:
    v_in_v: float
    v_out_v: float
    i_out_a: float
    f_sw_hz: float
    duty_cycle: float  # D = V_out / V_in (CCM, no losses)
    L_h: float  # inductor for given ΔI ripple
    Cout_f: float  # output cap for given ΔV ripple
    Cout_esr_max_ohm: float  # max ESR before ESR ripple swamps cap ripple
    inductor_peak_a: float  # peak inductor current
    inductor_rms_a: float  # rms inductor current (for sizing)
    expected_efficiency_pct: float
    notes: list[str]


def design_buck(
    *,
    v_in_v: float,
    v_out_v: float,
    i_out_a: float,
    f_sw_hz: float = 1e6,
    inductor_ripple_pct: float = 30.0,
    output_ripple_mvpp: float = 20.0,
    expected_efficiency_pct: float = 90.0,
) -> BuckDesign:
    """Size L and Cout for a buck SMPS at one operating point.

    Inputs match what you'd normally specify up-front:
    - ``f_sw_hz``: switching frequency (1 MHz typical for handheld /
      portable; 200-500 kHz for higher-current rails to ease losses)
    - ``inductor_ripple_pct``: ΔI_L / I_out_avg, typically 20-40%
    - ``output_ripple_mvpp``: target peak-to-peak output voltage ripple

    Equations (standard, Erickson "Fundamentals of Power Electronics"
    Ch.6 for CCM):
        D     = V_out / V_in
        ΔI_L  = V_out · (1 - D) / (L · f_sw)
        L     = V_out · (1 - D) / (ΔI_L · f_sw)
        ΔV_C  ≈ ΔI_L / (8 · f_sw · C)         (cap ripple, ESR=0)
        Cout  = ΔI_L / (8 · f_sw · ΔV_C)
        Cout_ESR_max = ΔV_C / ΔI_L            (ESR ripple = cap ripple → 50/50)
    """
    if v_in_v <= 0 or v_out_v <= 0 or i_out_a <= 0 or f_sw_hz <= 0:
        raise ValueError("All voltage / current / freq inputs must be positive")
    if v_out_v >= v_in_v:
        raise ValueError(f"Buck requires V_out < V_in; got V_in={v_in_v}, V_out={v_out_v}")

    duty = v_out_v / v_in_v
    ripple_a = i_out_a * inductor_ripple_pct / 100.0
    l_h = (v_out_v * (1 - duty)) / (ripple_a * f_sw_hz)

    ripple_v = output_ripple_mvpp * 1e-3
    cout_f = ripple_a / (8 * f_sw_hz * ripple_v)
    esr_max = ripple_v / ripple_a

    i_peak = i_out_a + ripple_a / 2
    # RMS (CCM, sawtooth on top of DC): I_rms² = I_avg² + ΔI²/12
    i_rms = math.sqrt(i_out_a**2 + (ripple_a**2) / 12)

    notes = []
    if duty < 0.1:
        notes.append(
            f"Duty cycle {duty * 100:.1f}% is low — minimum on-time of the "
            f"controller may limit operation. Consider a lower V_in or 2-stage."
        )
    if duty > 0.85:
        notes.append(
            f"Duty cycle {duty * 100:.1f}% is high — efficiency drops, "
            f"controllers struggle. Consider an LDO post-regulator instead."
        )
    if i_peak > 5.0:
        notes.append(
            f"Peak inductor current {i_peak:.1f} A is high; check inductor "
            f"saturation rating (I_sat) > peak."
        )

    return BuckDesign(
        v_in_v=v_in_v,
        v_out_v=v_out_v,
        i_out_a=i_out_a,
        f_sw_hz=f_sw_hz,
        duty_cycle=duty,
        L_h=l_h,
        Cout_f=cout_f,
        Cout_esr_max_ohm=esr_max,
        inductor_peak_a=i_peak,
        inductor_rms_a=i_rms,
        expected_efficiency_pct=expected_efficiency_pct,
        notes=notes,
    )
