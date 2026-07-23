"""Microwave directional coupler synthesis.

Closed-form dimensions for the four most common topologies:

- **Branch-line** (90° hybrid, quadrature): four λ/4 sections, two
  impedances Z₀ and Z₀/√2.
- **Rat-race** (180° hybrid): six λ/4 sections, single Z₀√2 ring.
- **Coupled-line** (backward-wave coupler): even/odd-mode impedance pair.
- **Lange coupler**: 4-finger interdigitated; same Ze/Zo as coupled-line
  but tighter coupling per unit length.

References:
- D. Pozar, "Microwave Engineering" 4th ed., §7.4–7.7
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from mcp_qucs_s.microstrip import (
    Substrate,
    synthesize_microstrip_line,
)

CouplerKind = Literal["branch_line", "rat_race", "coupled_line", "lange"]


@dataclass
class CouplerDesign:
    kind: CouplerKind
    coupling_db: float
    freq_hz: float
    z0: float
    substrate: Substrate
    # Per-section dimensions. Not dict[str, float]: every section also
    # carries a "role" string naming the arm it describes.
    sections: list[dict[str, Any]]
    notes: list[str]


def synthesize_coupler(
    kind: CouplerKind,
    coupling_db: float,
    freq_hz: float,
    z0: float,
    substrate: Substrate,
) -> CouplerDesign:
    """Synthesize coupler dimensions for the given topology + coupling level."""
    if coupling_db <= 0:
        raise ValueError(f"coupling_db must be >0 (positive dB), got {coupling_db}")
    if z0 <= 0:
        raise ValueError(f"z0 must be positive, got {z0}")

    if kind == "branch_line":
        return _branch_line(coupling_db, freq_hz, z0, substrate)
    if kind == "rat_race":
        return _rat_race(coupling_db, freq_hz, z0, substrate)
    if kind in ("coupled_line", "lange"):
        return _coupled_line(kind, coupling_db, freq_hz, z0, substrate)
    raise ValueError(f"Unknown coupler kind: {kind}")


def _branch_line(
    coupling_db: float, freq_hz: float, z0: float, substrate: Substrate
) -> CouplerDesign:
    """Standard 3 dB branch-line hybrid. The coupling is fixed by topology
    at 3 dB (equal split) for a single-section design; we accept the
    parameter but emit a warning if the user asks for anything else.
    """
    notes = []
    if abs(coupling_db - 3.0) > 0.5:
        notes.append(
            f"Single-section branch-line is fixed at 3 dB coupling; "
            f"{coupling_db} dB requires a multi-section design."
        )
    z_series = z0 / math.sqrt(2.0)  # series arms
    z_shunt = z0  # shunt arms

    series = synthesize_microstrip_line(z_series, 90.0, freq_hz, substrate)
    shunt = synthesize_microstrip_line(z_shunt, 90.0, freq_hz, substrate)
    return CouplerDesign(
        kind="branch_line",
        coupling_db=3.0,
        freq_hz=freq_hz,
        z0=z0,
        substrate=substrate,
        sections=[
            {
                "role": "series_arm_top",
                "z0": z_series,
                "length_mm": series.length_mm,
                "width_mm": series.width_mm,
            },
            {
                "role": "series_arm_bot",
                "z0": z_series,
                "length_mm": series.length_mm,
                "width_mm": series.width_mm,
            },
            {
                "role": "shunt_arm_left",
                "z0": z_shunt,
                "length_mm": shunt.length_mm,
                "width_mm": shunt.width_mm,
            },
            {
                "role": "shunt_arm_right",
                "z0": z_shunt,
                "length_mm": shunt.length_mm,
                "width_mm": shunt.width_mm,
            },
        ],
        notes=notes,
    )


def _rat_race(coupling_db: float, freq_hz: float, z0: float, substrate: Substrate) -> CouplerDesign:
    """180° rat-race hybrid: six λ/4 sections of the same Z₀√2 ring."""
    notes = []
    if abs(coupling_db - 3.0) > 0.5:
        notes.append("Rat-race is fundamentally a 3 dB coupler.")
    z_ring = z0 * math.sqrt(2.0)
    seg = synthesize_microstrip_line(z_ring, 90.0, freq_hz, substrate)
    # Total ring is 3λ/4 + 3·λ/4 = 1.5 λ
    return CouplerDesign(
        kind="rat_race",
        coupling_db=3.0,
        freq_hz=freq_hz,
        z0=z0,
        substrate=substrate,
        sections=[
            {
                "role": "ring_segment_quarter_wave",
                "z0": z_ring,
                "length_mm": seg.length_mm,
                "width_mm": seg.width_mm,
                "count": 6,
            },
        ],
        notes=notes,
    )


def _coupled_line(
    kind: CouplerKind, coupling_db: float, freq_hz: float, z0: float, substrate: Substrate
) -> CouplerDesign:
    """Coupled-line / Lange backward-wave coupler.

    Coupling factor C = 10^(-|coupling_db|/20) (linear voltage ratio)
    Even-mode impedance:   Ze = Z₀ · √((1+C)/(1-C))
    Odd-mode impedance:    Zo = Z₀ · √((1-C)/(1+C))
    """
    c_lin = 10 ** (-coupling_db / 20.0)
    ze = z0 * math.sqrt((1 + c_lin) / (1 - c_lin))
    zo = z0 * math.sqrt((1 - c_lin) / (1 + c_lin))

    # Single-line synthesis approximates trace width (true coupled-line
    # synthesis needs the gap → use Akhtarzad / Edwards approximations
    # for finer accuracy)
    even_line = synthesize_microstrip_line(ze, 90.0, freq_hz, substrate)
    odd_line = synthesize_microstrip_line(zo, 90.0, freq_hz, substrate)

    notes = [
        "Trace width derived from single-line model; exact coupled-line "
        "geometry (gap s, width w) requires Akhtarzad equations or EM sim.",
    ]
    if kind == "lange":
        notes.append(
            "Lange coupler uses 4 interdigitated fingers — typical lambda/4 "
            "section length scales by ~0.5 vs straight coupled lines for the "
            "same effective coupling."
        )
    return CouplerDesign(
        kind=kind,
        coupling_db=coupling_db,
        freq_hz=freq_hz,
        z0=z0,
        substrate=substrate,
        sections=[
            {
                "role": "even_mode",
                "z0": ze,
                "length_mm": even_line.length_mm,
                "width_mm_approx": even_line.width_mm,
            },
            {
                "role": "odd_mode",
                "z0": zo,
                "length_mm": odd_line.length_mm,
                "width_mm_approx": odd_line.width_mm,
            },
        ],
        notes=notes,
    )
