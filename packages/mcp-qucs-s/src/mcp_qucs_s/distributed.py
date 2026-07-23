"""Distributed-element filter synthesis (issue #27).

First topology: the stepped-impedance LPF (Pozar §8.6) — a lumped LPF
prototype realised as alternating short sections of very-high and
very-low impedance line:

- series inductor ``L`` → high-Z section, ``βl = ω_c·L / Z_h`` radians
- shunt capacitor ``C`` → low-Z section, ``βl = ω_c·C·Z_l`` radians

both evaluated at the cutoff. The short-line approximation behind the
mapping assumes ``βl < π/4``; sections that exceed it are flagged in the
returned notes rather than rejected (Pozar's own worked example runs one
section to 46°).

Input is a lumped components dict as produced by the ``mcp-ltspice``
synthesis tools — the same composition contract as
:func:`mcp_qucs_s.richards.lumped_to_distributed`.

References:
- D. Pozar, "Microwave Engineering" 4th ed., §8.6 (eqs. 8.86a/b)
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from numpy.typing import NDArray

from mcp_qucs_s.microstrip import Substrate, synthesize_microstrip_line
from mcp_qucs_s.richards import _refdes_index


def stepped_impedance_lpf(
    components: dict[str, float],
    cutoff_hz: float,
    *,
    z0: float = 50.0,
    z_high: float,
    z_low: float,
    substrate: Substrate,
) -> dict[str, Any]:
    """Map a lumped LPF ladder to stepped-impedance microstrip sections.

    ``z_high`` / ``z_low`` are the section impedances — ``z_high`` as high
    as fabrication allows (narrow trace), ``z_low`` as low as practical
    (wide trace); the further apart, the better the approximation.
    Returns a dict with the ordered section list (role, impedance,
    electrical length at cutoff, microstrip width/length) plus notes.
    """
    if not components:
        raise ValueError("stepped_impedance_lpf: no components to map")
    if z_high <= z0:
        raise ValueError(f"z_high ({z_high}) must exceed the system z0 ({z0})")
    if z_low >= z0:
        raise ValueError(f"z_low ({z_low}) must be below the system z0 ({z0})")

    omega_c = 2.0 * math.pi * cutoff_hz
    sections: list[dict[str, Any]] = []
    notes: list[str] = []
    long_sections: list[str] = []

    for name in sorted(components.keys(), key=_refdes_index):
        value = components[name]
        if name.startswith("L"):
            role, z_section = "high_z", z_high
            beta_l_rad = omega_c * value / z_high  # Pozar 8.86a
        elif name.startswith("C"):
            role, z_section = "low_z", z_low
            beta_l_rad = omega_c * value * z_low  # Pozar 8.86b
        else:
            raise ValueError(f"Component {name!r} is neither an inductor nor a capacitor")

        beta_l_deg = math.degrees(beta_l_rad)
        line = synthesize_microstrip_line(z_section, beta_l_deg, cutoff_hz, substrate)
        if beta_l_deg > 45.0:
            long_sections.append(f"{name} ({beta_l_deg:.1f}°)")
        sections.append(
            {
                "refdes": name,
                "role": role,
                "z0_ohm": z_section,
                "electrical_length_deg": beta_l_deg,
                "width_mm": line.width_mm,
                "length_mm": line.length_mm,
                "er_eff": line.eff_permittivity,
            }
        )

    if long_sections:
        notes.append(
            "Sections exceeding the βl < 45° short-line approximation: "
            + ", ".join(long_sections)
            + ". The realised cutoff will shift; consider a more extreme "
            "z_high/z_low ratio or a lower order."
        )
    notes.append(
        f"Electrical lengths are at f_c = {cutoff_hz / 1e6:.0f} MHz. The "
        "stepped-impedance response is approximate (no sharp transmission "
        "zeros) and re-enters periodically above the stopband."
    )

    return {
        "cutoff_hz": cutoff_hz,
        "z0": z0,
        "z_high": z_high,
        "z_low": z_low,
        "n_sections": len(sections),
        "sections": sections,
        "total_length_mm": sum(s["length_mm"] for s in sections),
        "notes": notes,
    }


def tline_cascade_sparams(
    sections: list[tuple[float, float]],
    freq_hz: NDArray[np.float64],
    f_ref_hz: float,
    *,
    z0_system: float = 50.0,
) -> NDArray[np.complex128]:
    """S-parameters of cascaded ideal (dispersionless, lossless)
    transmission-line sections.

    ``sections`` is an ordered source-to-load list of
    ``(z0_ohm, electrical_length_deg_at_f_ref)`` pairs; each section's
    phase scales linearly with frequency, ``θ(f) = θ_ref · f / f_ref`` —
    exactly the model qucsator uses for ``TLIN`` (probe-verified: vacuum
    velocity, so physical length only fixes θ at one frequency, which is
    all the cascade needs).

    Returns S of shape (npoints, 2, 2).
    """
    theta_ref = np.asarray([math.radians(t) for _, t in sections])
    z_line = np.asarray([z for z, _ in sections])

    a = np.ones_like(freq_hz, dtype=np.complex128)
    b = np.zeros_like(a)
    c = np.zeros_like(a)
    d = np.ones_like(a)

    for zi, ti in zip(z_line, theta_ref, strict=True):
        theta = ti * freq_hz / f_ref_hz
        cos_t = np.cos(theta)
        sin_t = np.sin(theta)
        ea = cos_t
        eb = 1j * zi * sin_t
        ec = 1j * sin_t / zi
        ed = cos_t
        a, b, c, d = (
            a * ea + b * ec,
            a * eb + b * ed,
            c * ea + d * ec,
            c * eb + d * ed,
        )

    with np.errstate(divide="ignore", invalid="ignore"):
        denom = a + b / z0_system + c * z0_system + d
        s11 = (a + b / z0_system - c * z0_system - d) / denom
        s21 = 2.0 / denom

    s = np.zeros((freq_hz.size, 2, 2), dtype=np.complex128)
    s[:, 0, 0] = s11
    s[:, 0, 1] = s21  # det(ABCD) = 1 per section ⇒ S12 = S21
    s[:, 1, 0] = s21
    s[:, 1, 1] = (-a + b / z0_system - c * z0_system + d) / denom
    return s
