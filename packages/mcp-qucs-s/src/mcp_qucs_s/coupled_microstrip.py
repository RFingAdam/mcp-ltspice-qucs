"""Coupled-microstrip even/odd-mode analysis and (W, S) synthesis.

Quasi-static Garg-Bahl capacitance model (Garg & Bahl 1979, as given in
Gupta et al. *Microstrip Lines and Slotlines* and Hong & Lancaster
§4.2.1): each mode's line capacitance is assembled from the parallel-
plate term, the single-line fringe capacitance (reusing this package's
Hammerstad-Jensen single-line functions), a shielded even-mode fringe,
and the odd-mode gap capacitances in air (exact elliptic-integral
ratio, scipy) and in the dielectric. Mode impedance and effective
permittivity follow from the capacitance with and without dielectric:

    εre = C/C_air,   Z0 = 1/(c·√(C·C_air))

Accuracy is a few percent over 0.1 ≤ W/h ≤ 10, 0.1 ≤ S/h ≤ 5 — the
integration tests hold it against qucsator's Kirschning MCOUPLED model.
Synthesis inverts the analysis numerically for (W, S).
"""

from __future__ import annotations

import math

from scipy.optimize import least_squares
from scipy.special import ellipk

from mcp_qucs_s.microstrip import (
    C0,
    Substrate,
    _eff_permittivity,
    _impedance,
    synthesize_width,
)

EPS0 = 8.8541878128e-12  # F/m

#: Garg-Bahl validity window in normalized coordinates u = W/h, g = S/h.
_U_MIN, _U_MAX = 0.1, 10.0
_G_MIN, _G_MAX = 0.05, 10.0


def _single_line_caps(u: float, er: float) -> tuple[float, float]:
    """(C_plate, C_fringe) of a single microstrip of width ratio ``u``.

    Total line capacitance comes from the H-J closed form via
    ``C = √εre / (c·Z0)``; the fringe part is what exceeds the
    parallel-plate term, split evenly between the two edges.
    """
    ere = _eff_permittivity(u, er)
    z0 = _impedance(u, ere)
    c_total = math.sqrt(ere) / (C0 * z0)
    c_plate = EPS0 * er * u
    c_fringe = (c_total - c_plate) / 2.0
    return c_plate, c_fringe


def _mode_caps(u: float, g: float, er: float) -> tuple[float, float]:
    """(C_even, C_odd) per unit length for one dielectric constant."""
    c_plate, c_fringe = _single_line_caps(u, er)

    # Even mode: the outer edge keeps the single-line fringe; the inner
    # edge's fringe is reduced by the magnetic wall at the symmetry plane.
    a = math.exp(-0.1 * math.exp(2.33 - 2.53 * u))
    c_fringe_inner = c_fringe / (1.0 + (a / g) * math.tanh(8.0 * g))
    c_even = c_plate + c_fringe + c_fringe_inner

    # Odd mode: the symmetry plane is an electric wall; the inner edge
    # couples through the gap in air (exact elliptic ratio K(k')/K(k))
    # and through the dielectric.
    k = g / (g + 2.0 * u)
    c_gap_air = EPS0 * ellipk(1.0 - k * k) / ellipk(k * k)
    c_gap_diel = (EPS0 * er / math.pi) * math.log(1.0 / math.tanh(math.pi * g / 4.0)) + (
        0.65 * c_fringe * (0.02 * math.sqrt(er) / g + 1.0 - er**-2)
    )
    c_odd = c_plate + c_fringe + c_gap_air + c_gap_diel
    return c_even, c_odd


def analyze_coupled_microstrip(
    width_mm: float,
    gap_mm: float,
    substrate: Substrate,
) -> dict[str, float]:
    """Even/odd-mode impedances and effective permittivities of a
    symmetric coupled microstrip pair (quasi-static, t = 0).

    Returns ``{z0e_ohm, z0o_ohm, er_eff_e, er_eff_o, w_h_ratio,
    s_h_ratio}``.
    """
    if width_mm <= 0 or gap_mm <= 0:
        raise ValueError(f"width_mm and gap_mm must be positive; got {width_mm}, {gap_mm}")
    u = width_mm / substrate.h_mm
    g = gap_mm / substrate.h_mm

    ce, co = _mode_caps(u, g, substrate.er)
    ce_air, co_air = _mode_caps(u, g, 1.0)

    return {
        "z0e_ohm": 1.0 / (C0 * math.sqrt(ce * ce_air)),
        "z0o_ohm": 1.0 / (C0 * math.sqrt(co * co_air)),
        "er_eff_e": ce / ce_air,
        "er_eff_o": co / co_air,
        "w_h_ratio": u,
        "s_h_ratio": g,
    }


def synthesize_coupled_microstrip(
    z0e_ohm: float,
    z0o_ohm: float,
    substrate: Substrate,
) -> tuple[float, float]:
    """Invert the analysis: (Z0e, Z0o) → (width_mm, gap_mm).

    Solved in log-coordinates over the Garg-Bahl validity window; a
    target the window cannot reach (e.g. coupler-grade Ze/Zo ratios that
    edge-coupled microstrip cannot realise) raises rather than returning
    a bound-clipped corner.
    """
    if z0e_ohm <= z0o_ohm:
        raise ValueError(f"z0e_ohm ({z0e_ohm}) must exceed z0o_ohm ({z0o_ohm})")
    h = substrate.h_mm

    def residuals(x: list[float]) -> list[float]:
        u = math.exp(x[0])
        g = math.exp(x[1])
        ce, co = _mode_caps(u, g, substrate.er)
        ce_air, co_air = _mode_caps(u, g, 1.0)
        ze = 1.0 / (C0 * math.sqrt(ce * ce_air))
        zo = 1.0 / (C0 * math.sqrt(co * co_air))
        return [ze - z0e_ohm, zo - z0o_ohm]

    u0 = synthesize_width(math.sqrt(z0e_ohm * z0o_ohm), substrate) / h
    u0 = min(max(u0, _U_MIN * 1.5), _U_MAX / 1.5)
    res = least_squares(
        residuals,
        [math.log(u0), math.log(1.0)],
        bounds=(
            [math.log(_U_MIN), math.log(_G_MIN)],
            [math.log(_U_MAX), math.log(_G_MAX)],
        ),
        xtol=1e-14,
        ftol=1e-14,
    )
    worst = max(map(abs, res.fun))
    if not res.success or worst > 0.005 * min(z0e_ohm, z0o_ohm):
        raise ValueError(
            f"Unrealizable coupled-microstrip target Z0e={z0e_ohm:.2f} Ω / "
            f"Z0o={z0o_ohm:.2f} Ω on this substrate (residual {worst:.3f} Ω): "
            "the required coupling is outside the edge-coupled geometry range. "
            "Reduce the bandwidth or use a different topology."
        )
    return math.exp(res.x[0]) * h, math.exp(res.x[1]) * h
