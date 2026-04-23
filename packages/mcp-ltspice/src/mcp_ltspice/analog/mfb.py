"""Multiple-Feedback (MFB) 2nd-order active filter synthesis.

Inverting topology, single op-amp, generally better Q-handling than
Sallen-Key (good up to Q ≈ 20-25). The MFB output is inverted relative
to the input — flip the next stage's polarity, or accept it as-is for
audio / signal-processing chains where absolute polarity is irrelevant.

Refs:
- Sedra & Smith, "Microelectronic Circuits" 7th ed., §17.5
- TI SLOA049 "Active Low-Pass Filter Design"
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal


@dataclass
class MFBDesign:
    topology: Literal["lpf", "bpf"]
    fc_hz: float
    q: float
    gain_v_v: float  # in-band gain (always negative for MFB; magnitude reported)
    R1: float
    R2: float
    R3: float
    C1: float
    C2: float
    op_amp_min_gbw_hz: float
    notes: list[str]


def mfb_low_pass(
    fc_hz: float,
    q: float = 1 / math.sqrt(2),
    gain_v_v: float = 1.0,
    *,
    c_pf: float = 1000.0,
) -> MFBDesign:
    """Synthesize a Multiple-Feedback LPF.

    Standard inverting MFB LPF transfer function:
        H(s) = -R2/R1 · 1 / (1 + s·C1·(R2 + R3 + R2·R3/R1) + s²·C1·C2·R2·R3)

    Design equations (Sedra-Smith 17.55):
        ω₀² = 1 / (R2 · R3 · C1 · C2)
        ω₀ / Q = (1/C1) · (1/R2 + 1/R3 + 1/R1)
        K = -R2 / R1            (in-band gain, inverting)

    With C2 chosen freely and C1 = m·C2 (typically m ≥ 4·Q²·(1+K)):
        R3 = ω₀⁻¹ · √(C1/(C2·R2·R1)) ... and so on
    Practical recipe (Mancini, "Op Amps for Everyone" §16.4):
        Pick C1, then:
          C2 ≥ C1 · 4·Q²·(1+K)        (positive R3 requirement)
          R2 = (α + √(α² - 4·β·C2/C1)) / (2·β)
          R1 = R2 / K
          R3 = 1 / (ω₀² · R2 · C1 · C2)
        where α = 1/(ω₀·C2·Q),  β = (1+K)/(ω₀·Q)/C2... [we use a
        simplified well-conditioned form below].
    """
    if fc_hz <= 0 or q <= 0 or gain_v_v <= 0:
        raise ValueError("fc_hz, q, gain_v_v must all be positive")

    omega_0 = 2 * math.pi * fc_hz
    k = gain_v_v
    c1 = c_pf * 1e-12

    # Mancini's design recipe: pick C2 to ensure real positive R values.
    # Minimum: C2 ≥ C1 · 4·Q²·(1+K). Use 1.5× minimum for margin.
    c2_min = c1 * 4 * q * q * (1 + k)
    c2 = 1.5 * c2_min

    # Now solve for R2 from the quadratic:
    #   R2² + R2·(C1/(ω_0·C1·C2·Q·(1+K))·...) - ... = 0
    # Easier: use the closed-form from Mancini Table 16-3:
    #   R2 = (1 + √(1 - 4·Q²·(1+K)·C1/C2)) / (2·Q·ω_0·C1)
    # But our choice c2 = 1.5·c2_min ensures the discriminant is real.
    discriminant = 1 - 4 * q * q * (1 + k) * c1 / c2
    if discriminant < 0:
        raise ValueError(
            f"Negative discriminant - C2 ratio too small. Try increasing c_pf "
            f"or lowering Q. (Q={q}, K={k}, C2/C1={c2 / c1:.1f}.)"
        )
    r2 = (1 + math.sqrt(discriminant)) / (2 * q * omega_0 * c1)
    r1 = r2 / k
    r3 = 1.0 / (omega_0 * omega_0 * r2 * c1 * c2)

    notes: list[str] = [
        "MFB output is inverted (180° phase). Flip the polarity in the next stage or accept it.",
    ]
    if q > 25:
        notes.append(
            f"Q={q:.1f} is at MFB's practical limit. Consider biquad or "
            f"state-variable for higher Q."
        )

    min_gbw = 100 * fc_hz * q
    notes.append(f"Op-amp GBW must exceed ~{min_gbw / 1e6:.1f} MHz at Q={q:.2f}.")

    return MFBDesign(
        topology="lpf",
        fc_hz=fc_hz,
        q=q,
        gain_v_v=k,
        R1=r1,
        R2=r2,
        R3=r3,
        C1=c1,
        C2=c2,
        op_amp_min_gbw_hz=min_gbw,
        notes=notes,
    )


def mfb_band_pass(
    fc_hz: float,
    q: float = 5.0,
    gain_v_v: float = 1.0,
    *,
    c_pf: float = 100.0,
) -> MFBDesign:
    """Multiple-Feedback BPF — Mancini's classical 3-resistor 2-cap design.

    Equations (Sedra-Smith 17.61):
        ω₀² = (R1 + R3) / (R1 · R2 · R3 · C1 · C2)
        ω₀ / Q = (1/C1 + 1/C2) / R2
        K = -R2 / (2·R1)        (gain at center freq, inverting)

    With C1 = C2 = C, R1 = R3 simplifies to:
        ω₀ = √2 / (R · C · √(R/R2))
        Q  = ω₀ · R2 · C / 2
        K  = -R2 / (2·R1)
    """
    if fc_hz <= 0 or q <= 0 or gain_v_v <= 0:
        raise ValueError("fc_hz, q, gain_v_v must all be positive")

    omega_0 = 2 * math.pi * fc_hz
    c = c_pf * 1e-12
    # With C1 = C2 = C: R2 = 2·Q / (ω_0 · C)
    r2 = 2 * q / (omega_0 * c)
    # Gain K = -R2 / (2·R1) → R1 = R2 / (2·K) = Q/(K·ω_0·C)
    r1 = r2 / (2 * gain_v_v)
    # R3 from ω₀² = (R1 + R3)/(R1·R2·R3·C²):
    # → R3 = R1 · 1/(R1·R2·C²·ω₀² - 1)
    denom = r1 * r2 * c * c * omega_0 * omega_0 - 1
    if denom <= 0:
        raise ValueError("MFB BPF: invalid Q/gain combo. Try smaller gain_v_v or larger Q.")
    r3 = r1 / denom

    notes = ["MFB BPF output is inverted (180° phase)."]
    if q > 25:
        notes.append(f"Q={q:.1f} is at the practical limit.")
    min_gbw = 100 * fc_hz * q
    notes.append(f"Op-amp GBW must exceed ~{min_gbw / 1e6:.1f} MHz at Q={q:.2f}.")

    return MFBDesign(
        topology="bpf",
        fc_hz=fc_hz,
        q=q,
        gain_v_v=gain_v_v,
        R1=r1,
        R2=r2,
        R3=r3,
        C1=c,
        C2=c,
        op_amp_min_gbw_hz=min_gbw,
        notes=notes,
    )
