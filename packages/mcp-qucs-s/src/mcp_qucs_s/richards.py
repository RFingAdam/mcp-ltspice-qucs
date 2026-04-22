"""Lumped-to-distributed conversion via Richards transformation + Kuroda identities.

Richards' transformation maps a lumped LC ladder to a network of
short-circuited and open-circuited stubs. Practically useful for
converting an LPF prototype into a microstrip realization at
frequencies where component self-resonance becomes a problem (typically
above ~3 GHz with 0402 parts).

References:
- D. Pozar, "Microwave Engineering" 4th ed., §8.5
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from mcp_qucs_s.microstrip import Substrate, synthesize_microstrip_line


@dataclass
class DistributedElement:
    role: str  # 'series_short_stub', 'shunt_open_stub', 'connecting_line'
    z0: float  # characteristic impedance
    electrical_length_deg: float
    width_mm: float
    length_mm: float
    physical_freq_hz: float


def lumped_to_distributed(
    components: dict[str, float],
    cutoff_hz: float,
    *,
    z0: float = 50.0,
    substrate: Substrate,
    apply_kuroda: bool = True,
) -> dict[str, Any]:
    """Convert an LC lowpass prototype to its distributed-element equivalent.

    Richards transformation: tan(βl) = Ω so each lumped element becomes
    a length-l stub at the design frequency. We use l = λ/8 at the
    cutoff frequency (the standard choice).

    - Series inductor L → series short-circuited stub of impedance Z = ωL
    - Shunt capacitor C → shunt open-circuited stub of admittance Y = ωC
      (i.e. characteristic impedance Z = 1/(ωC))

    With ``apply_kuroda=True``, redundant connecting lines are inserted
    between the stubs to make the design realizable in microstrip — the
    series stubs become shunt stubs after Kuroda's first identity.

    Returns a dict with the ordered element list + notes.
    """
    omega_c = 2 * math.pi * cutoff_hz
    elements: list[DistributedElement] = []
    notes: list[str] = []

    # Walk components in numeric order
    import re

    sorted_names = sorted(components.keys(), key=lambda n: int(re.search(r"\d+", n).group()))

    for name in sorted_names:
        value = components[name]
        if name.startswith("L"):
            z_stub = omega_c * value  # ω_c * L
            line = synthesize_microstrip_line(z_stub, 45.0, cutoff_hz, substrate)
            elements.append(
                DistributedElement(
                    role="series_short_stub",
                    z0=z_stub,
                    electrical_length_deg=45.0,  # λ/8 at fc
                    width_mm=line.width_mm,
                    length_mm=line.length_mm,
                    physical_freq_hz=cutoff_hz,
                )
            )
        elif name.startswith("C"):
            y_stub = omega_c * value
            z_stub = 1.0 / y_stub
            line = synthesize_microstrip_line(z_stub, 45.0, cutoff_hz, substrate)
            elements.append(
                DistributedElement(
                    role="shunt_open_stub",
                    z0=z_stub,
                    electrical_length_deg=45.0,
                    width_mm=line.width_mm,
                    length_mm=line.length_mm,
                    physical_freq_hz=cutoff_hz,
                )
            )

    if apply_kuroda:
        # Insert λ/8 connecting lines of impedance Z₀ between stubs.
        # Kuroda's first identity then turns series short stubs into
        # shunt open stubs (realizable in microstrip).
        connecting = synthesize_microstrip_line(z0, 45.0, cutoff_hz, substrate)
        kuroda_elements: list[DistributedElement] = []
        for i, elt in enumerate(elements):
            kuroda_elements.append(elt)
            if i < len(elements) - 1:
                kuroda_elements.append(
                    DistributedElement(
                        role="connecting_line",
                        z0=z0,
                        electrical_length_deg=45.0,
                        width_mm=connecting.width_mm,
                        length_mm=connecting.length_mm,
                        physical_freq_hz=cutoff_hz,
                    )
                )
        elements = kuroda_elements
        notes.append(
            "Kuroda identities applied — series short stubs become shunt "
            "open stubs after absorbing connecting lines. Realizable in "
            "microstrip without via short-circuits."
        )

    notes.append(
        f"Stub electrical length = 45° (λ/8 at f_c = {cutoff_hz / 1e6:.0f} MHz). "
        f"Response is periodic with period 4·f_c."
    )

    return {
        "cutoff_hz": cutoff_hz,
        "z0": z0,
        "kuroda_applied": apply_kuroda,
        "n_elements": len(elements),
        "elements": [
            {
                "role": e.role,
                "z0_ohm": e.z0,
                "electrical_length_deg": e.electrical_length_deg,
                "width_mm": e.width_mm,
                "length_mm": e.length_mm,
            }
            for e in elements
        ],
        "notes": notes,
    }
