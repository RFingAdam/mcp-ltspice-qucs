"""Near-end / far-end crosstalk between two coupled traces.

First-order analytical estimates from the standard coupled-line theory.
For more accuracy, use a 2.5D field solver (the openEMS MCP has tools
for that) — these closed forms are for early architecture decisions.

Refs:
- E. Bogatin, "Signal and Power Integrity — Simplified", §10
"""

from __future__ import annotations

import math


def estimate_next_db(
    *,
    coupling_length_mm: float,
    trace_separation_mm: float,
    substrate_height_mm: float,
    rise_time_ps: float,
    er: float = 4.0,
) -> dict[str, float]:
    """Near-End Crosstalk (NEXT) estimate.

    NEXT is approximately:
        K_NEXT = (1/4) · (Lm/L0 + Cm/C0)
    where Lm and Cm are mutual L and C, normalised by the line's own L0
    and C0. For coupled microstrip with separation s and height h:
        Lm/L0 ≈ 1 / (1 + (s/h)²)        (loose approximation)
        Cm/C0 ≈ similar form

    NEXT amplitude saturates when t_rise > 2·t_TD (round-trip on the
    coupled section). Below that it grows linearly with t_TD/t_rise.
    """
    if coupling_length_mm <= 0 or trace_separation_mm <= 0 or substrate_height_mm <= 0:
        raise ValueError("Lengths must be positive")
    if rise_time_ps <= 0:
        raise ValueError("rise_time_ps must be positive")

    # Time delay through coupled section
    v_p_mm_per_ns = 300 / math.sqrt(er)  # ~150 mm/ns for εr=4
    t_d_ns = coupling_length_mm / v_p_mm_per_ns
    t_d_ps = t_d_ns * 1000

    # Coupling factor (loose closed form)
    s_h = trace_separation_mm / substrate_height_mm
    k_couple = 1 / (1 + s_h**2)
    k_next = 0.25 * 2 * k_couple  # both L and C contribute

    # Amplitude scaling
    if rise_time_ps > 2 * t_d_ps:
        # Saturated: only partial transition is reflected
        next_factor = (2 * t_d_ps) / rise_time_ps
    else:
        next_factor = 1.0

    next_db = 20 * math.log10(max(k_next * next_factor, 1e-9))

    return {
        "k_next": k_next * next_factor,
        "next_db": next_db,
        "t_delay_ps": t_d_ps,
        "saturated": rise_time_ps > 2 * t_d_ps,
    }


def estimate_fext_db(
    *,
    coupling_length_mm: float,
    trace_separation_mm: float,
    substrate_height_mm: float,
    rise_time_ps: float,
    er: float = 4.0,
) -> dict[str, float]:
    """Far-End Crosstalk (FEXT) estimate.

    FEXT amplitude is:
        K_FEXT = -(1/2) · (Lm/L0 - Cm/C0) · (t_d / t_rise)
    Note FEXT can be zero in stripline (where Lm/L0 ≈ Cm/C0) but is
    significant in microstrip due to different field distributions.

    FEXT grows linearly with coupling length while NEXT saturates,
    so on long buses FEXT becomes dominant.
    """
    if coupling_length_mm <= 0 or trace_separation_mm <= 0 or substrate_height_mm <= 0:
        raise ValueError("Lengths must be positive")
    if rise_time_ps <= 0:
        raise ValueError("rise_time_ps must be positive")

    v_p_mm_per_ns = 300 / math.sqrt(er)
    t_d_ps = (coupling_length_mm / v_p_mm_per_ns) * 1000

    s_h = trace_separation_mm / substrate_height_mm
    # In microstrip, Cm/C0 < Lm/L0 typically by 20-30%
    lm_l0 = 1 / (1 + s_h**2)
    cm_c0 = lm_l0 * 0.7  # microstrip approximation
    k_fext = 0.5 * abs(lm_l0 - cm_c0) * (t_d_ps / rise_time_ps)

    # Saturate at 100% (physical max)
    k_fext = min(k_fext, 1.0)
    fext_db = 20 * math.log10(max(k_fext, 1e-9))

    return {
        "k_fext": k_fext,
        "fext_db": fext_db,
        "t_delay_ps": t_d_ps,
    }
