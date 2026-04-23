"""Higher-order active filters as a cascade of 2nd-order stages.

The standard approach for any nth-order (n≥3) active LPF: factor the
prototype Butterworth / Bessel / Chebyshev polynomial into 2nd-order
sections (and one 1st-order if n is odd). Each 2nd-order section is
then realized as a Sallen-Key or MFB stage with its own (fc, Q).

Tabulated stage Qs come from standard filter tables (Mancini Table 16-1
for Butterworth, Mancini Table 16-2 for Bessel, etc.).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from mcp_ltspice.analog.sallen_key import SallenKeyDesign, sallen_key_low_pass


@dataclass
class CascadeStage:
    order: int  # 1 or 2
    fc_normalized: float  # fc relative to the overall design fc
    q: float | None  # None for 1st-order stages
    section: SallenKeyDesign | None


# Butterworth: all-pole, max-flat passband
# Each row is one nth-order filter; entries are (fc_norm, Q) per stage,
# 1st-order stage encoded as Q = None.
# Source: Mancini "Op Amps for Everyone" Table 16-3
_BUTTERWORTH_STAGES: dict[int, list[tuple[float, float | None]]] = {
    2: [(1.0, 0.7071)],
    3: [(1.0, 1.0), (1.0, None)],
    4: [(1.0, 0.5412), (1.0, 1.3066)],
    5: [(1.0, 0.6180), (1.0, 1.6180), (1.0, None)],
    6: [(1.0, 0.5176), (1.0, 0.7071), (1.0, 1.9319)],
    7: [(1.0, 0.5550), (1.0, 0.8019), (1.0, 2.2470), (1.0, None)],
    8: [(1.0, 0.5098), (1.0, 0.6013), (1.0, 0.9000), (1.0, 2.5629)],
}


# Bessel: max-flat group delay, gentler roll-off
# Source: Williams "Electronic Filter Design Handbook" Table 11-39
_BESSEL_STAGES: dict[int, list[tuple[float, float | None]]] = {
    2: [(1.2742, 0.5773)],
    3: [(1.4524, 0.6910), (1.3270, None)],
    4: [(1.4192, 0.5219), (1.5912, 0.8055)],
    5: [(1.5611, 0.5635), (1.7607, 0.9165), (1.5069, None)],
    6: [(1.6060, 0.5103), (1.6913, 0.6112), (1.9071, 1.0233)],
}


def second_order_stages_for_order(
    order: int,
    response: Literal["butterworth", "bessel"] = "butterworth",
) -> list[tuple[float, float | None]]:
    """Return the (fc_norm, Q) list for each stage."""
    table = _BUTTERWORTH_STAGES if response == "butterworth" else _BESSEL_STAGES
    if order not in table:
        raise ValueError(f"Order {order} not in {response} stage table. Available: {sorted(table)}")
    return table[order]


def cascaded_lpf_design(
    fc_hz: float,
    order: int,
    *,
    response: Literal["butterworth", "bessel"] = "butterworth",
    topology: Literal["sallen_key"] = "sallen_key",
    c_pf: float = 1000.0,
) -> dict[str, object]:
    """Synthesize an nth-order LPF as a cascade of 2nd-order stages."""
    if fc_hz <= 0:
        raise ValueError("fc_hz must be positive")
    stages_table = second_order_stages_for_order(order, response)

    stages: list[CascadeStage] = []
    for fc_norm, q in stages_table:
        stage_fc = fc_hz * fc_norm
        if q is None:
            # 1st-order RC section (single resistor + cap, no op-amp Q)
            stages.append(
                CascadeStage(
                    order=1,
                    fc_normalized=fc_norm,
                    q=None,
                    section=None,
                )
            )
        else:
            section = sallen_key_low_pass(stage_fc, q=q, c_pf=c_pf)
            stages.append(
                CascadeStage(
                    order=2,
                    fc_normalized=fc_norm,
                    q=q,
                    section=section,
                )
            )

    # Required op-amp GBW = max across stages
    min_gbw = max(
        (s.section.op_amp_min_gbw_hz for s in stages if s.section is not None),
        default=0.0,
    )

    return {
        "fc_hz": fc_hz,
        "order": order,
        "response": response,
        "topology": topology,
        "n_stages": len(stages),
        "stages": [
            {
                "stage_index": i + 1,
                "order": s.order,
                "fc_hz": fc_hz * s.fc_normalized,
                "q": s.q,
                "components": (
                    {
                        "R1": s.section.R1,
                        "R2": s.section.R2,
                        "C1": s.section.C1,
                        "C2": s.section.C2,
                        "R3": s.section.R3,
                        "R4": s.section.R4,
                        "gain_v_v": s.section.gain_v_v,
                    }
                    if s.section is not None
                    else None
                ),
                "notes": s.section.notes if s.section else ["1st-order RC: pick R = 1/(2πfc·C)."],
            }
            for i, s in enumerate(stages)
        ],
        "op_amp_min_gbw_hz": min_gbw,
        "n_op_amps_required": sum(1 for s in stages if s.section is not None),
    }


def transfer_function_db(
    fc_hz: float,
    order: int,
    response: Literal["butterworth", "bessel"] = "butterworth",
    *,
    n_freq_points: int = 401,
) -> dict[str, list[float]]:
    """Compute the cascaded |H(jω)| in dB across frequency.

    Useful for verifying a design before committing components — gives
    you the ideal-op-amp transfer function so you can compare with a
    real LTspice sim that includes op-amp non-idealities.
    """
    import numpy as np

    f = np.geomspace(fc_hz / 100, fc_hz * 100, n_freq_points)
    omega = 2 * math.pi * f
    omega_c = 2 * math.pi * fc_hz
    h = np.ones_like(omega, dtype=np.complex128)

    for fc_norm, q in second_order_stages_for_order(order, response):
        omega_stage = omega_c * fc_norm
        if q is None:
            # 1st-order: 1 / (1 + jω/ω_stage)
            h /= 1 + 1j * omega / omega_stage
        else:
            # 2nd-order: 1 / (1 - (ω/ω_stage)² + j·ω/(Q·ω_stage))
            denom = 1 - (omega / omega_stage) ** 2 + 1j * omega / (q * omega_stage)
            h /= denom

    return {
        "freq_hz": f.tolist(),
        "h_db": (20 * np.log10(np.abs(h))).tolist(),
        "h_phase_deg": np.degrees(np.angle(h)).tolist(),
    }
