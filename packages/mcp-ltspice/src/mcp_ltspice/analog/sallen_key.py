"""Sallen-Key 2nd-order active filter synthesis.

The Sallen-Key topology uses one op-amp + 2 resistors + 2 capacitors per
section. Pros: low component count, low sensitivity at low Q. Cons:
limited Q (typically <10 before component sensitivity gets ugly).

Component selection follows the standard "equal-component" approach
(R1 = R2, C1 = C2 for LP) which gives the simplest design at the cost
of a fixed Q for unity-gain configurations. For arbitrary Q we use
Sedra/Smith's classical equations.

Refs:
- Sedra & Smith, "Microelectronic Circuits" 7th ed., §17.5
- TI SLOA024 "Filter Design in Thirty Seconds"
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal


@dataclass
class SallenKeyDesign:
    """Component values for one Sallen-Key 2nd-order section."""

    topology: Literal["lpf", "hpf", "bpf"]
    fc_hz: float
    q: float
    gain_v_v: float
    R1: float
    R2: float
    R3: float | None  # gain-setting resistor (top of voltage divider)
    R4: float | None  # gain-setting resistor (bottom of voltage divider)
    C1: float
    C2: float
    op_amp_min_gbw_hz: float  # GBW the op-amp must exceed
    notes: list[str]


def _check_q_warning(q: float, max_q: float = 10.0) -> list[str]:
    """Component sensitivity blows up for Q above ~10 in Sallen-Key."""
    if q > max_q:
        return [
            f"Q={q:.1f} is high for Sallen-Key. Component sensitivity "
            f"scales as Q² — consider Multiple-Feedback (MFB) above Q=10."
        ]
    return []


def sallen_key_low_pass(
    fc_hz: float,
    q: float = 1 / math.sqrt(2),
    gain_v_v: float = 1.0,
    *,
    c_pf: float = 1000.0,
) -> SallenKeyDesign:
    """Synthesize a Sallen-Key LPF with the "C1 = C2" simplification.

    For unity gain (gain=1) and equal-C, the design reduces to:
        R1 = 1 / (Q · ω_c · C)
        R2 = Q / (ω_c · C)

    For arbitrary gain K:
        K = 1 + R3/R4   (non-inverting amplifier)
        Q = 1 / (3 - K)   for the equal-R, equal-C case
    """
    if fc_hz <= 0 or q <= 0 or gain_v_v <= 0:
        raise ValueError("fc_hz, q, gain_v_v must all be positive")

    omega_c = 2 * math.pi * fc_hz
    c = c_pf * 1e-12
    notes = _check_q_warning(q)

    # Sedra-Smith Sallen-Key LPF with equal C1 = C2 = C:
    # R1·R2 = 1/(ω_c² C²)
    # R1 + (1-K)·R2 = 1/(Q·ω_c·C)   for non-unity gain
    # For unity gain (K=1): R1 = 1/(Q ω_c C), R2 = Q/(ω_c C)
    if abs(gain_v_v - 1.0) < 1e-6:
        r1 = 1.0 / (q * omega_c * c)
        r2 = q / (omega_c * c)
        r3, r4 = None, None
    else:
        # General case: still equal C, derive R1, R2 from gain + Q
        # Pick R1 = R2 = R for simplicity, then:
        #   ω_c² = 1/(R²·C²) → R = 1/(ω_c·C)
        #   Q = 1 / (3 - K) → K = 3 - 1/Q
        # If user-specified gain differs from 3 - 1/Q, we can't satisfy
        # both with R1 = R2. Fall back to:
        r = 1.0 / (omega_c * c)
        r1 = r2 = r
        # K = 3 - 1/Q for equal-R; emit warning if user gain disagrees
        k_natural = 3 - 1.0 / q
        if abs(gain_v_v - k_natural) > 0.1:
            notes.append(
                f"Equal-R Sallen-Key with Q={q:.2f} naturally gives K={k_natural:.2f}. "
                f"Requested gain {gain_v_v} can be achieved by adding a separate "
                f"non-inverting buffer with the desired gain."
            )
        # Pick R4 = 10 kΩ standard, R3 = (K-1) · R4
        r4 = 10e3
        r3 = (gain_v_v - 1.0) * r4

    # Op-amp GBW rule of thumb: at least 100 × fc · Q
    min_gbw = 100 * fc_hz * q
    notes.append(f"Op-amp GBW must exceed ~{min_gbw / 1e6:.1f} MHz at Q={q:.2f}.")

    return SallenKeyDesign(
        topology="lpf",
        fc_hz=fc_hz,
        q=q,
        gain_v_v=gain_v_v,
        R1=r1,
        R2=r2,
        R3=r3,
        R4=r4,
        C1=c,
        C2=c,
        op_amp_min_gbw_hz=min_gbw,
        notes=notes,
    )


def sallen_key_high_pass(
    fc_hz: float,
    q: float = 1 / math.sqrt(2),
    *,
    r_kohm: float = 10.0,
) -> SallenKeyDesign:
    """Sallen-Key HPF with equal-R simplification (R1 = R2 = R).

    For equal R and unity gain:
        C1 = 1/(Q ω_c R)
        C2 = Q/(ω_c R)
    """
    if fc_hz <= 0 or q <= 0:
        raise ValueError("fc_hz and q must be positive")

    omega_c = 2 * math.pi * fc_hz
    r = r_kohm * 1e3
    notes = _check_q_warning(q)
    c1 = 1.0 / (q * omega_c * r)
    c2 = q / (omega_c * r)
    min_gbw = 100 * fc_hz * q
    notes.append(f"Op-amp GBW must exceed ~{min_gbw / 1e6:.1f} MHz at Q={q:.2f}.")
    return SallenKeyDesign(
        topology="hpf",
        fc_hz=fc_hz,
        q=q,
        gain_v_v=1.0,
        R1=r,
        R2=r,
        R3=None,
        R4=None,
        C1=c1,
        C2=c2,
        op_amp_min_gbw_hz=min_gbw,
        notes=notes,
    )


def sallen_key_band_pass(
    fc_hz: float,
    q: float = 1.0,
    *,
    r_kohm: float = 10.0,
) -> SallenKeyDesign:
    """Sallen-Key band-pass (single op-amp, equal-R, equal-C).

    Configuration: R1 from input to V+, C1 from V+ to ground, R2 from V+
    to op-amp output (feedback), C2 in feedback path. With R1 = R2 = R,
    C1 = C2 = C: ω₀ = √2/(R·C), Q determined by amplifier gain.

    For Q-controlled gain configuration:
        K = 3 - √2/Q   (gain at f₀)
        ω₀ = √2 / (R·C)
        → C = √2 / (R · ω₀)
    """
    if fc_hz <= 0 or q <= 0:
        raise ValueError("fc_hz and q must be positive")
    omega_0 = 2 * math.pi * fc_hz
    r = r_kohm * 1e3
    c = math.sqrt(2) / (r * omega_0)
    notes = _check_q_warning(q)
    k = 3 - math.sqrt(2) / q
    gain = k
    r4 = 10e3
    r3 = (gain - 1.0) * r4
    min_gbw = 100 * fc_hz * q
    notes.append(
        f"Sallen-Key BPF gain at f₀ is fixed at {gain:.2f} V/V by the Q. "
        f"Add an external attenuator if you need a different gain."
    )
    notes.append(f"Op-amp GBW must exceed ~{min_gbw / 1e6:.1f} MHz at Q={q:.2f}.")
    return SallenKeyDesign(
        topology="bpf",
        fc_hz=fc_hz,
        q=q,
        gain_v_v=gain,
        R1=r,
        R2=r,
        R3=r3,
        R4=r4,
        C1=c,
        C2=c,
        op_amp_min_gbw_hz=min_gbw,
        notes=notes,
    )
