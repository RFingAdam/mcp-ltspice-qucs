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

import itertools
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


def interdigital_bpf(
    g: list[float],
    f0_hz: float,
    fractional_bandwidth: float,
    *,
    z0: float = 50.0,
    substrate: Substrate,
    z_resonator_ohm: float = 70.0,
) -> dict[str, Any]:
    """Interdigital BPF: N coupled λ/4 resonators, alternately shorted,
    tapped input/output — designed on the exact same-velocity TEM array
    model of :mod:`mcp_qucs_s.multiconductor`.

    Design identities (standard coupled-resonator, no table lookups):

    - ``k_{i,i+1} = Δ/√(g_i·g_{i+1})`` — realised by inverting the
      *closed-form* interdigital pair-resonance split for the mutual
      admittance ``y_m`` (``cosθ = ±y_m/Y_r``).
    - ``Qe = g0·g1/Δ`` — realised by tapping the end resonators at
      ``θ_t = arcsin(√(π·Y_r/(4·G0·Qe)))`` from the shorted end, from
      the shorted-λ/4 slope parameter ``b = (π/4)·Y_r`` (isolated-
      resonator approximation; the achieved response is what the exact
      array solver and qucsator validate).

    Every line's total self-admittance is ``Y_r = 1/z_resonator_ohm``;
    the graph-model stub admittance ``Y_r − Σ y_m`` must stay positive,
    otherwise the Δ / Z_r combination is unrealizable and this raises.

    Physical (W, S) per adjacent pair comes from the Garg-Bahl inversion
    of ``Z0e = 1/(Y_r − y_m)``, ``Z0o = 1/(Y_r + y_m)`` — a first-cut
    per-pair mapping (an interior line is shared by two pairs and the
    quasi-TEM even/odd velocities differ); EM-refine for hardware.
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

    from mcp_qucs_s.multiconductor import mutual_for_k

    order = len(g) - 2
    delta = fractional_bandwidth
    y_r = 1.0 / z_resonator_ohm
    g0_load = 1.0 / z0

    k_targets = [
        delta / math.sqrt(g[i] * g[i + 1]) for i in range(1, order)
    ]  # k_{i,i+1}, i = 1..N-1
    y_mutual = [mutual_for_k(k) * y_r for k in k_targets]

    y_stub: list[float] = []
    for i in range(order):
        left = y_mutual[i - 1] if i > 0 else 0.0
        right = y_mutual[i] if i < order - 1 else 0.0
        stub = y_r - left - right
        if stub <= 0.0:
            raise ValueError(
                f"Unrealizable: resonator {i + 1}'s stub admittance goes non-positive "
                f"({stub:.4g} S) — the bandwidth is too wide for {z_resonator_ohm:.0f} Ω "
                "resonators. Narrow Δ or lower z_resonator_ohm."
            )
        y_stub.append(stub)

    def tap_deg(qe: float) -> float:
        arg = math.pi * y_r / (4.0 * g0_load * qe)
        if arg > 1.0:
            raise ValueError(
                f"Unrealizable tap: Qe={qe:.2f} is below the end-fed minimum "
                f"π·Y_r/(4·G0) = {math.pi * y_r / (4.0 * g0_load):.2f} for "
                f"{z_resonator_ohm:.0f} Ω resonators — the bandwidth is too wide."
            )
        return math.degrees(math.asin(math.sqrt(arg)))

    qe_in = g[0] * g[1] / delta
    qe_out = g[order] * g[order + 1] / delta
    tap_in = tap_deg(qe_in)
    tap_out = tap_deg(qe_out)

    # Alternating shorts: line i (1-indexed) shorted at bottom for odd i.
    bottom = ["short" if i % 2 == 1 else "open" for i in range(1, order + 1)]
    top = ["open" if i % 2 == 1 else "short" for i in range(1, order + 1)]

    # Tap heights measured from the global bottom (from each end line's
    # OWN shorted end): line 1 is always bottom-shorted; line N depends
    # on parity.
    h_in = tap_in
    h_out = tap_out if order % 2 == 1 else 90.0 - tap_out
    breakpoints = sorted({round(h, 12) for h in (h_in, h_out) if 0.0 < h < 90.0})
    boundaries = [0.0, *breakpoints, 90.0]
    segments_deg = [b - a for a, b in itertools.pairwise(boundaries)]
    level_of = {round(h, 12): i + 1 for i, h in enumerate(breakpoints)}
    ports = [(level_of[round(h_in, 12)], 0), (level_of[round(h_out, 12)], order - 1)]

    y_c = np.zeros((order, order))
    for i in range(order):
        y_c[i, i] = y_r
    for i, ym in enumerate(y_mutual):
        y_c[i, i + 1] = -ym
        y_c[i + 1, i] = -ym

    # Physical per-pair dimensions + resonator lengths
    couplings: list[dict[str, Any]] = []
    er_effs: list[float] = []
    for i, ym in enumerate(y_mutual):
        z0e = 1.0 / (y_r - ym)
        z0o = 1.0 / (y_r + ym)
        w_mm, s_mm = synthesize_coupled_microstrip(z0e, z0o, substrate)
        modes = analyze_coupled_microstrip(w_mm, s_mm, substrate)
        er_effs.append((modes["er_eff_e"] + modes["er_eff_o"]) / 2.0)
        couplings.append(
            {
                "between": (i + 1, i + 2),
                "k": k_targets[i],
                "y_mutual": ym,
                "z0e_ohm": z0e,
                "z0o_ohm": z0o,
                "width_mm": w_mm,
                "gap_mm": s_mm,
            }
        )
    er_avg = sum(er_effs) / len(er_effs) if er_effs else substrate.er
    length_mm = C0 / (4.0 * f0_hz * math.sqrt(er_avg)) * 1e3

    resonators = [
        {
            "index": i + 1,
            "z_ohm": z_resonator_ohm,
            "y_total": y_r,
            "y_stub": y_stub[i],
            "shorted_end": "bottom" if bottom[i] == "short" else "top",
            "length_mm": length_mm,
        }
        for i in range(order)
    ]

    result: dict[str, Any] = {
        "f0_hz": f0_hz,
        "fractional_bandwidth": delta,
        "z0": z0,
        "order": order,
        "resonators": resonators,
        "couplings": couplings,
        "tap_deg": tap_in,
        "tap_deg_out": tap_out,
        "tap_mm": tap_in / 90.0 * length_mm,
        "y_c": y_c,
        "segments_deg": segments_deg,
        "bottom": bottom,
        "top": top,
        "ports": ports,
        "notes": [
            f"Tapped feed at {tap_in:.2f}° from the shorted end (slope-parameter "
            "formula, isolated-resonator approximation — see 'achieved' for the "
            "response the exact array model actually delivers).",
            "Physical (W, S) is a first-cut per-pair Garg-Bahl mapping: an interior "
            "line is shared by two pairs (widths averaged by construction here since "
            "all lines carry the same Y_r) and quasi-TEM even/odd velocities differ; "
            "EM-refine before hardware.",
            "Ideal-TEM electrical model is exact (same-velocity assumption) and is "
            "what the graph netlist simulates.",
        ],
    }

    # Self-report what the design actually achieves on the exact model —
    # the tapped-feed approximation degrades ripple vs the prototype, and
    # an MCP consumer should see that number, not assume the spec.
    from mcp_qucs_s.multiconductor import segmented_array_sparams

    f_grid = np.linspace(f0_hz * (1.0 - 3.0 * delta), f0_hz * (1.0 + 3.0 * delta), 1201)
    s = segmented_array_sparams(
        y_c,
        f_grid,
        f0_hz,
        segments_deg=segments_deg,
        bottom=bottom,
        top=top,
        ports=ports,
        z0_system=z0,
    )
    s21_db = 20.0 * np.log10(np.maximum(np.abs(s[:, 1, 0]), 1e-12))
    s11_db = 20.0 * np.log10(np.maximum(np.abs(s[:, 0, 0]), 1e-12))
    pk = int(np.argmax(s21_db))
    above = f_grid[s21_db > s21_db[pk] - 3.0]
    in_band = np.abs(f_grid - f0_hz) < (delta / 2.0) * f0_hz * 0.9
    result["achieved"] = {
        "peak_db": float(s21_db[pk]),
        "f_peak_hz": float(f_grid[pk]),
        "band_center_hz": float(math.sqrt(above[0] * above[-1])),
        "bw_3db_frac": float((above[-1] - above[0]) / f0_hz),
        "worst_inband_return_loss_db": float(s11_db[in_band].max()),
    }
    return result


def combline_bpf(
    g: list[float],
    f0_hz: float,
    fractional_bandwidth: float,
    *,
    z0: float = 50.0,
    substrate: Substrate,
    z_resonator_ohm: float = 70.0,
    theta0_deg: float = 45.0,
) -> dict[str, Any]:
    """Combline BPF: N coupled lines shorted at the SAME end, each tuned
    by a lumped capacitor at the open end, resonator length θ0 < 90°
    (default 45°) — on the exact TEM array model.

    - Tuning: ``C = Y_r·cot(θ0)/ω0`` per resonator (pure TEM combline
      genuinely needs the caps — at θ0 = 90° there is no passband).
    - Couplings hit ``k = Δ/√(g_i·g_{i+1})`` by inverting the exact pair
      transcendental ``ωC = (Y_r ± y_m)·cot(θ(ω))``
      (:func:`mcp_qucs_s.multiconductor.combline_pair_split`).
    - Tapped feed from the loaded resonator's slope parameter
      ``b = (Y_r/2)(cotθ0 + θ0·csc²θ0)``:
      ``θ_t = arcsin(sinθ0·√(b/(G0·Qe)))`` — reduces to the interdigital
      formula at θ0 = 90°.
    - Upper stopband is the topology's selling point: the next resonance
      branch needs cotθ > 0 again, so a 45° combline is clean to ≈ 4·f0
      (vs the edge-coupled spurious at 2·f0).

    Same honesty mechanisms as the interdigital synthesis: stub
    admittances must stay positive, taps must be realizable, and the
    returned ``achieved`` block reports the exact-model response.
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
    if not 10.0 <= theta0_deg < 90.0:
        raise ValueError(f"theta0_deg must be in [10, 90); got {theta0_deg}")

    from scipy.optimize import brentq

    from mcp_qucs_s.multiconductor import combline_pair_split, segmented_array_sparams

    order = len(g) - 2
    delta = fractional_bandwidth
    y_r = 1.0 / z_resonator_ohm
    g0_load = 1.0 / z0
    t0 = math.radians(theta0_deg)
    c_load = y_r / math.tan(t0) / (2.0 * math.pi * f0_hz)

    def pair_k(y_m: float) -> float:
        fl, fh = combline_pair_split(y_r, y_m, theta0_deg, f0_hz)
        return (fh * fh - fl * fl) / (fh * fh + fl * fl)

    k_targets = [delta / math.sqrt(g[i] * g[i + 1]) for i in range(1, order)]
    y_mutual: list[float] = []
    for k in k_targets:
        if k >= pair_k(0.95 * y_r):
            raise ValueError(
                f"Unrealizable coupling k={k:.3f} for {z_resonator_ohm:.0f} Ω "
                f"combline resonators at θ0={theta0_deg:.0f}° — narrow Δ."
            )
        y_mutual.append(float(brentq(lambda ym, kt=k: pair_k(ym) - kt, 1e-9 * y_r, 0.95 * y_r)))

    y_stub: list[float] = []
    for i in range(order):
        left = y_mutual[i - 1] if i > 0 else 0.0
        right = y_mutual[i] if i < order - 1 else 0.0
        stub = y_r - left - right
        if stub <= 0.0:
            raise ValueError(
                f"Unrealizable: resonator {i + 1}'s stub admittance goes non-positive "
                f"({stub:.4g} S) — the bandwidth is too wide for {z_resonator_ohm:.0f} Ω "
                "resonators. Narrow Δ or lower z_resonator_ohm."
            )
        y_stub.append(stub)

    slope_b = (y_r / 2.0) * (math.cos(t0) / math.sin(t0) + t0 / math.sin(t0) ** 2)

    def tap_deg(qe: float) -> float:
        arg = math.sin(t0) * math.sqrt(slope_b / (g0_load * qe))
        if arg > 1.0:
            raise ValueError(
                f"Unrealizable tap: Qe={qe:.2f} needs arcsin({arg:.2f}) — the "
                "bandwidth is too wide for a tapped combline at this Z_resonator."
            )
        return math.degrees(math.asin(arg))

    qe_in = g[0] * g[1] / delta
    qe_out = g[order] * g[order + 1] / delta
    tap_in = tap_deg(qe_in)
    tap_out = tap_deg(qe_out)

    bottom = ["short"] * order
    top = ["open"] * order
    breakpoints = sorted({round(h, 12) for h in (tap_in, tap_out) if 0.0 < h < theta0_deg})
    boundaries = [0.0, *breakpoints, theta0_deg]
    segments_deg = [b - a for a, b in itertools.pairwise(boundaries)]
    level_of = {round(h, 12): i + 1 for i, h in enumerate(breakpoints)}
    ports = [(level_of[round(tap_in, 12)], 0), (level_of[round(tap_out, 12)], order - 1)]
    n_levels = len(segments_deg)
    cap_loads = [(n_levels, i, c_load) for i in range(order)]

    y_c = np.zeros((order, order))
    for i in range(order):
        y_c[i, i] = y_r
    for i, ym in enumerate(y_mutual):
        y_c[i, i + 1] = -ym
        y_c[i + 1, i] = -ym

    couplings: list[dict[str, Any]] = []
    er_effs: list[float] = []
    for i, ym in enumerate(y_mutual):
        z0e = 1.0 / (y_r - ym)
        z0o = 1.0 / (y_r + ym)
        w_mm, s_mm = synthesize_coupled_microstrip(z0e, z0o, substrate)
        modes = analyze_coupled_microstrip(w_mm, s_mm, substrate)
        er_effs.append((modes["er_eff_e"] + modes["er_eff_o"]) / 2.0)
        couplings.append(
            {
                "between": (i + 1, i + 2),
                "k": k_targets[i],
                "y_mutual": ym,
                "z0e_ohm": z0e,
                "z0o_ohm": z0o,
                "width_mm": w_mm,
                "gap_mm": s_mm,
            }
        )
    er_avg = sum(er_effs) / len(er_effs) if er_effs else substrate.er
    length_mm = (theta0_deg / 360.0) * C0 / (f0_hz * math.sqrt(er_avg)) * 1e3

    resonators = [
        {
            "index": i + 1,
            "z_ohm": z_resonator_ohm,
            "y_total": y_r,
            "y_stub": y_stub[i],
            "shorted_end": "bottom",
            "length_mm": length_mm,
            "c_load_farad": c_load,
        }
        for i in range(order)
    ]

    result: dict[str, Any] = {
        "f0_hz": f0_hz,
        "fractional_bandwidth": delta,
        "z0": z0,
        "order": order,
        "theta0_deg": theta0_deg,
        "resonators": resonators,
        "couplings": couplings,
        "tap_deg": tap_in,
        "tap_deg_out": tap_out,
        "tap_mm": tap_in / theta0_deg * length_mm,
        "y_c": y_c,
        "segments_deg": segments_deg,
        "bottom": bottom,
        "top": top,
        "ports": ports,
        "cap_loads": cap_loads,
        "notes": [
            f"Combline at θ0 = {theta0_deg:.0f}°: loading caps "
            f"{c_load * 1e12:.3f} pF tune each shorted stub to f0; the next "
            f"resonance branch sits near {180.0 / theta0_deg:.1f}·f0, so the "
            "upper stopband is clean well past 2·f0 (the edge-coupled spurious "
            "frequency).",
            f"Tapped feed at {tap_in:.2f}° from the short (loaded-resonator "
            "slope parameter, isolated-resonator approximation — see 'achieved').",
            "Physical (W, S) is a first-cut per-pair Garg-Bahl mapping; loading "
            "caps are ideal lumped elements (chip caps or screw tuners in "
            "hardware). EM-refine before hardware.",
        ],
    }

    f_grid = np.linspace(f0_hz * (1.0 - 3.0 * delta), f0_hz * (1.0 + 3.0 * delta), 1201)
    s = segmented_array_sparams(
        y_c,
        f_grid,
        f0_hz,
        segments_deg=segments_deg,
        bottom=bottom,
        top=top,
        ports=ports,
        cap_loads=cap_loads,
        z0_system=z0,
    )
    s21_db = 20.0 * np.log10(np.maximum(np.abs(s[:, 1, 0]), 1e-12))
    s11_db = 20.0 * np.log10(np.maximum(np.abs(s[:, 0, 0]), 1e-12))
    pk = int(np.argmax(s21_db))
    above = f_grid[s21_db > s21_db[pk] - 3.0]
    in_band = np.abs(f_grid - f0_hz) < (delta / 2.0) * f0_hz * 0.9
    result["achieved"] = {
        "peak_db": float(s21_db[pk]),
        "f_peak_hz": float(f_grid[pk]),
        "band_center_hz": float(math.sqrt(above[0] * above[-1])),
        "bw_3db_frac": float((above[-1] - above[0]) / f0_hz),
        "worst_inband_return_loss_db": float(s11_db[in_band].max()),
    }
    return result
