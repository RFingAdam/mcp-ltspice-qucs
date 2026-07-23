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

from mcp_qucs_s.coupled_microstrip import (
    analyze_coupled_microstrip,
    synthesize_coupled_microstrip,
)
from mcp_qucs_s.microstrip import C0, Substrate, synthesize_microstrip_line
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


def coupled_line_bpf(
    g: list[float],
    f0_hz: float,
    fractional_bandwidth: float,
    *,
    z0: float = 50.0,
    substrate: Substrate,
) -> dict[str, Any]:
    """Edge-coupled (parallel coupled-line) BPF synthesis (Pozar §8.7).

    ``g`` is the full prototype vector ``g0..g_{N+1}`` — exactly what the
    ``mcp-ltspice`` synthesis tools return as ``g_coefficients`` — so an
    order-N filter yields N+1 quarter-wave coupled sections via the
    J-inverter constants (Pozar 8.121):

        Z₀J₁     = √(πΔ / (2·g₀·g₁))
        Z₀Jₙ     = πΔ / (2·√(g_{n−1}·g_n))      n = 2..N
        Z₀J_{N+1} = √(πΔ / (2·g_N·g_{N+1}))
        Z₀e = Z₀(1 + JZ₀ + (JZ₀)²),  Z₀o = Z₀(1 − JZ₀ + (JZ₀)²)

    Each section's (W, S) comes from the Garg-Bahl inversion and its
    physical length is a quarter-wave at f₀ using the mean of the two
    mode permittivities. This is the electrical core of the hairpin
    filter as well — a hairpin is this filter with the resonators
    folded; the fold's slide factor is not modelled here.
    """
    if len(g) < 3:
        raise ValueError(
            f"g must be the full prototype vector g0..g_(N+1) with at least 3 entries "
            f"(order ≥ 1); got {len(g)}"
        )
    if not 0.0 < fractional_bandwidth < 1.0:
        raise ValueError(f"fractional_bandwidth must be in (0, 1); got {fractional_bandwidth}")
    if any(gi <= 0 for gi in g):
        raise ValueError("all g coefficients must be positive")

    order = len(g) - 2
    delta = fractional_bandwidth
    jz0: list[float] = [math.sqrt(math.pi * delta / (2.0 * g[0] * g[1]))]
    for n in range(2, order + 1):
        jz0.append(math.pi * delta / (2.0 * math.sqrt(g[n - 1] * g[n])))
    jz0.append(math.sqrt(math.pi * delta / (2.0 * g[order] * g[order + 1])))

    sections: list[dict[str, Any]] = []
    for n, j in enumerate(jz0, start=1):
        ze = z0 * (1.0 + j + j * j)
        zo = z0 * (1.0 - j + j * j)
        w_mm, s_mm = synthesize_coupled_microstrip(ze, zo, substrate)
        modes = analyze_coupled_microstrip(w_mm, s_mm, substrate)
        er_avg = (modes["er_eff_e"] + modes["er_eff_o"]) / 2.0
        length_mm = C0 / (4.0 * f0_hz * math.sqrt(er_avg)) * 1e3
        sections.append(
            {
                "index": n,
                "jz0": j,
                "z0e_ohm": ze,
                "z0o_ohm": zo,
                "electrical_length_deg": 90.0,
                "width_mm": w_mm,
                "gap_mm": s_mm,
                "length_mm": length_mm,
                "er_eff_e": modes["er_eff_e"],
                "er_eff_o": modes["er_eff_o"],
            }
        )

    return {
        "f0_hz": f0_hz,
        "fractional_bandwidth": delta,
        "z0": z0,
        "order": order,
        "n_sections": len(sections),
        "sections": sections,
        "notes": [
            "Each section is λ/4 at f0; physical length uses the mean of the "
            "even/odd effective permittivities (the two modes travel at "
            "different speeds — the classic edge-coupled spurious-response "
            "mechanism at 2·f0).",
            "Hairpin realisation: fold each resonator into a U; the coupled "
            "sections keep these (W, S, L) but the fold adds a slide factor "
            "not modelled here.",
        ],
    }


def coupled_section_sparams(
    sections: list[tuple[float, float]],
    freq_hz: NDArray[np.float64],
    f_ref_hz: float,
    *,
    z0_system: float = 50.0,
) -> NDArray[np.complex128]:
    """S-parameters of cascaded ideal coupled-line BPF sections.

    ``sections`` is an ordered list of ``(Z0e, Z0o)`` pairs, each a
    quarter-wave at ``f_ref_hz``, connected diagonally with the other
    two ports open (probe-verified to match qucsator's ``CTLIN`` to
    numerical precision). Per section, with θ = (π/2)·f/f_ref:

        A = D = (Z0e+Z0o)/(Z0e−Z0o)·cosθ
        B = j·[(Z0e−Z0o)² − (Z0e+Z0o)²·cos²θ] / [2·(Z0e−Z0o)·sinθ]
        C = j·2·sinθ/(Z0e−Z0o)

    Frequencies where sinθ = 0 (DC, 2·f0, ...) are transmission nulls of
    the open-circuited section; non-finite bins are filled with the
    fully-reflective limit (S11 = −1, S21 = 0).
    """
    theta = (math.pi / 2.0) * freq_hz / f_ref_hz

    a = np.ones_like(freq_hz, dtype=np.complex128)
    b = np.zeros_like(a)
    c = np.zeros_like(a)
    d = np.ones_like(a)

    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        for ze, zo in sections:
            zsum = ze + zo
            zdif = ze - zo
            cos_t = np.cos(theta)
            sin_t = np.sin(theta)
            ea = (zsum / zdif) * cos_t
            eb = 1j * (zdif**2 - zsum**2 * cos_t**2) / (2.0 * zdif * sin_t)
            ec = 1j * 2.0 * sin_t / zdif
            # section D equals A (symmetric two-port)
            a, b, c, d = (
                a * ea + b * ec,
                a * eb + b * ea,
                c * ea + d * ec,
                c * eb + d * ea,
            )

        denom = a + b / z0_system + c * z0_system + d
        s11 = (a + b / z0_system - c * z0_system - d) / denom
        s21 = 2.0 / denom
        s22 = (-a + b / z0_system - c * z0_system + d) / denom

    s = np.zeros((freq_hz.size, 2, 2), dtype=np.complex128)
    s[:, 0, 0] = np.where(np.isfinite(s11), s11, -1.0)
    s[:, 0, 1] = np.where(np.isfinite(s21), s21, 0.0)  # det = 1 ⇒ S12 = S21
    s[:, 1, 0] = s[:, 0, 1]
    s[:, 1, 1] = np.where(np.isfinite(s22), s22, -1.0)
    return s


def hairpin_bpf(
    g: list[float],
    f0_hz: float,
    fractional_bandwidth: float,
    *,
    z0: float = 50.0,
    substrate: Substrate,
    bend_mm: float | None = None,
) -> dict[str, Any]:
    """Hairpin BPF — the folded edge-coupled filter (Cristal-Frankel).

    Builds on :func:`coupled_line_bpf`: each half-wave resonator (line2
    of coupled section i joined to line1 of section i+1) is folded into
    a U, and the U-bend connector of physical length ``bend_mm`` (an
    MLIN at the mean arm width; default 3× that width ≈ arm spacing of
    2W plus two corners) adds electrical length θ_b to every resonator.
    To keep each resonator at exactly 180° at f₀, every coupled section
    is shortened to θ = 90° − θ_b/2 — exact when one bend length serves
    all resonators, which is what is done here.

    Shortening the sections below 90° weakens the J-inverter couplings
    slightly (the classic hairpin bandwidth-shrink trade); corner
    discontinuities of the U and cross-arm self-coupling of the fold are
    NOT modeled — they are the residual a field solver would refine.
    """
    base = coupled_line_bpf(g, f0_hz, fractional_bandwidth, z0=z0, substrate=substrate)
    sections = base["sections"]
    order = base["order"]

    mean_w = sum(s["width_mm"] for s in sections) / len(sections)
    if bend_mm is None:
        bend_mm = 3.0 * mean_w
    if bend_mm < 0:
        raise ValueError(f"bend_mm must be ≥ 0; got {bend_mm}")

    from mcp_qucs_s.microstrip import analyze_microstrip

    bend_line = analyze_microstrip(mean_w, substrate, f0_hz)
    theta_b = 360.0 * bend_mm / bend_line["wavelength_eff_mm"]
    theta_section = 90.0 - theta_b / 2.0
    if theta_section < 30.0:
        raise ValueError(
            f"bend_mm={bend_mm:.2f} mm is {theta_b:.1f}° at f0 — shortening the "
            f"coupled sections to {theta_section:.1f}° leaves too little coupled "
            "length to realise the filter. Use a shorter bend or a thinner substrate."
        )

    for s in sections:
        s["length_mm"] = s["length_mm"] * (theta_section / 90.0)
        s["electrical_length_deg"] = theta_section

    resonators = [
        {
            "index": i,
            "arm1_deg": sections[i - 1]["electrical_length_deg"],
            "arm1_mm": sections[i - 1]["length_mm"],
            "bend_deg": theta_b,
            "bend_mm": bend_mm,
            "arm2_deg": sections[i]["electrical_length_deg"],
            "arm2_mm": sections[i]["length_mm"],
        }
        for i in range(1, order + 1)
    ]

    base.update(
        {
            "kind": "hairpin",
            "bend_mm": bend_mm,
            "bend_deg": theta_b,
            "bend_width_mm": mean_w,
            "resonators": resonators,
        }
    )
    base["notes"] = [
        "Folded edge-coupled (hairpin-line) design: every coupled section is "
        f"shortened to {theta_section:.2f}° so each resonator's "
        "arm + bend + arm totals exactly 180° at f0. The shortened sections "
        "weaken the couplings slightly — the classic hairpin bandwidth trade.",
        "Unmodeled residuals: the U's two corner discontinuities (bend "
        "capacitance) and cross-arm self-coupling between the folded arms of "
        "one resonator. Refine with a field solver if the layout is tight.",
    ]
    return base
