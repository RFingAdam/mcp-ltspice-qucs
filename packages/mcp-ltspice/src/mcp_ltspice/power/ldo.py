"""Linear regulator (LDO) analysis.

First-order analytical models for the metrics that decide whether an
LDO is the right choice and what bypass network it needs:

- **Headroom / dropout**: V_in - V_out at min I_out
- **Efficiency**: P_out / P_in (for linear regs, ≈ V_out / V_in at high I)
- **Power dissipation**: (V_in - V_out) · I_out
- **Output ripple from a noisy V_in**: V_ripple_out ≈ V_ripple_in / PSRR_lin
- **Output noise** estimate from the LDO's spec'd noise + bypass cap

Useful as a sanity check before committing a part — answers "do I need
an LDO or an SMPS, and which LDO?" without spinning up a SPICE sim.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class LDOAnalysis:
    v_in_v: float
    v_out_v: float
    i_out_a: float
    headroom_v: float  # V_in - V_out
    dissipation_w: float  # (V_in - V_out) · I_out
    efficiency_pct: float  # 100 · V_out / V_in
    psrr_db: float
    output_ripple_uvpp: float | None  # if v_ripple_in_mvpp provided
    notes: list[str]


def analyze_ldo(
    *,
    v_in_v: float,
    v_out_v: float,
    i_out_a: float,
    dropout_v: float = 0.3,
    psrr_db: float = 60.0,
    v_ripple_in_mvpp: float | None = None,
) -> LDOAnalysis:
    """Analyze an LDO at one operating point.

    Inputs are scalar parameters from the LDO datasheet:
    - ``dropout_v``: minimum V_in - V_out before the regulator falls out
      of regulation (typical: 0.1-0.5 V for modern LDOs)
    - ``psrr_db``: power-supply rejection ratio at the ripple frequency
      (depends on frequency; pass the value at the dominant ripple
      component, often 100 kHz - 1 MHz for switching upstream)
    - ``v_ripple_in_mvpp``: optional input ripple amplitude

    Returns a :class:`LDOAnalysis` with margin / dissipation / output-
    ripple estimates.
    """
    if v_in_v <= 0 or v_out_v <= 0 or i_out_a < 0:
        raise ValueError("v_in_v, v_out_v must be positive; i_out_a >= 0")

    headroom = v_in_v - v_out_v
    if headroom < dropout_v:
        notes = [
            f"WARNING: headroom ({headroom * 1000:.0f} mV) < dropout "
            f"({dropout_v * 1000:.0f} mV). LDO will fall out of regulation."
        ]
    else:
        notes = []

    dissipation = headroom * i_out_a
    efficiency = 100.0 * v_out_v / v_in_v if v_in_v > 0 else 0.0

    # Output ripple from input ripple via PSRR
    output_ripple_uvpp: float | None = None
    if v_ripple_in_mvpp is not None and v_ripple_in_mvpp > 0:
        psrr_lin = 10 ** (psrr_db / 20.0)
        output_ripple_uvpp = (v_ripple_in_mvpp * 1000.0) / psrr_lin

    if dissipation > 1.0:
        notes.append(
            f"Dissipation {dissipation:.2f} W is high — needs heatsink "
            f"or thermal pad. Consider a buck SMPS for V_in/V_out > 2."
        )
    if efficiency < 50:
        notes.append(
            f"Efficiency only {efficiency:.0f}% — buck SMPS would be >85% at this V_in/V_out ratio."
        )

    return LDOAnalysis(
        v_in_v=v_in_v,
        v_out_v=v_out_v,
        i_out_a=i_out_a,
        headroom_v=headroom,
        dissipation_w=dissipation,
        efficiency_pct=efficiency,
        psrr_db=psrr_db,
        output_ripple_uvpp=output_ripple_uvpp,
        notes=notes,
    )


def required_psrr_for_ripple_target(
    *,
    v_ripple_in_mvpp: float,
    v_ripple_out_uvpp_max: float,
) -> float:
    """How much PSRR (in dB) does the LDO need to meet an output-ripple target?

    Useful when picking between LDO families: e.g. an SMPS upstream
    gives 50 mV pp ripple, you want < 100 µV pp on the rail to feed an
    ADC reference — required PSRR is 20·log10(50000/100) = 54 dB at
    the SMPS switching frequency.
    """
    if v_ripple_in_mvpp <= 0 or v_ripple_out_uvpp_max <= 0:
        raise ValueError("Both ripple values must be positive")
    return 20 * math.log10((v_ripple_in_mvpp * 1000.0) / v_ripple_out_uvpp_max)
