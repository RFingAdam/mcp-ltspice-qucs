"""Boost (step-up) SMPS component sizing — CCM mode."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class BoostDesign:
    v_in_v: float
    v_out_v: float
    i_out_a: float
    f_sw_hz: float
    duty_cycle: float  # D = 1 - V_in/V_out (CCM, no losses)
    L_h: float
    Cout_f: float
    Cout_esr_max_ohm: float
    inductor_peak_a: float
    inductor_rms_a: float
    notes: list[str]


def design_boost(
    *,
    v_in_v: float,
    v_out_v: float,
    i_out_a: float,
    f_sw_hz: float = 500e3,
    inductor_ripple_pct: float = 30.0,
    output_ripple_mvpp: float = 50.0,
) -> BoostDesign:
    """Size a boost SMPS at one operating point (CCM).

    Equations (Erickson Ch.6):
        D       = 1 - V_in / V_out
        I_in    = I_out / (1 - D)            (CCM, lossless)
        ΔI_L    = V_in · D / (L · f_sw)
        L       = V_in · D / (ΔI_L · f_sw)
        ΔV_C    ≈ I_out · D / (C · f_sw)
        Cout    = I_out · D / (ΔV_C · f_sw)
        Cout_ESR_max = ΔV_C / I_out          (ESR ripple budget)
    """
    if v_in_v <= 0 or v_out_v <= 0 or i_out_a <= 0 or f_sw_hz <= 0:
        raise ValueError("All voltage / current / freq inputs must be positive")
    if v_out_v <= v_in_v:
        raise ValueError(f"Boost requires V_out > V_in; got V_in={v_in_v}, V_out={v_out_v}")

    duty = 1 - v_in_v / v_out_v
    i_in_avg = i_out_a / (1 - duty)
    ripple_a = i_in_avg * inductor_ripple_pct / 100.0
    l_h = (v_in_v * duty) / (ripple_a * f_sw_hz)

    ripple_v = output_ripple_mvpp * 1e-3
    cout_f = (i_out_a * duty) / (ripple_v * f_sw_hz)
    esr_max = ripple_v / i_out_a

    i_peak = i_in_avg + ripple_a / 2
    i_rms = math.sqrt(i_in_avg**2 + (ripple_a**2) / 12)

    notes = []
    if duty > 0.8:
        notes.append(
            f"Duty cycle {duty * 100:.1f}% is high — large step-up ratio. "
            f"Consider a 2-stage boost or a SEPIC for efficiency."
        )
    if i_peak > 8.0:
        notes.append(f"Peak inductor current {i_peak:.1f} A is high — check I_sat rating.")

    return BoostDesign(
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
        notes=notes,
    )
