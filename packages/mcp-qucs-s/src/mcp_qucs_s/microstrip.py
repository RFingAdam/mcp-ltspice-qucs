"""Microstrip transmission-line synthesis.

Closed-form Hammerstad-Jensen equations for characteristic impedance,
effective permittivity, and inverse synthesis (Z₀, electrical length →
W, L). Accurate to within 1% for the practical range W/h = 0.1..20 and
ε_r = 1..15.

References:
- E. Hammerstad, Ø. Jensen, "Accurate Models for Microstrip
  Computer-Aided Design", IEEE MTT-S, 1980
- D. Pozar, "Microwave Engineering" 4th ed., §3.8
"""

from __future__ import annotations

import math
from dataclasses import dataclass

C0 = 299_792_458.0  # m/s


@dataclass
class Substrate:
    """Microstrip substrate parameters."""

    er: float  # relative permittivity (e.g. 4.4 for FR-4, 3.55 for Rogers 4350B)
    h_mm: float  # substrate height (dielectric thickness) in mm
    t_um: float = 35.0  # copper thickness in microns (1 oz = 35 µm)
    tan_d: float = 0.02  # loss tangent

    def h_m(self) -> float:
        return self.h_mm * 1e-3

    def t_m(self) -> float:
        return self.t_um * 1e-6


@dataclass
class MicrostripLine:
    """Result of a microstrip synthesis or analysis call."""

    z0: float  # characteristic impedance (Ω)
    width_mm: float  # trace width (mm)
    length_mm: float  # physical length (mm), or 0 if not applicable
    eff_permittivity: float  # effective dielectric constant
    electrical_length_deg: float
    freq_hz: float
    substrate: Substrate
    wavelength_eff_mm: float
    metadata: dict[str, float]


# --------------------------------------------------------------------------
# Hammerstad-Jensen analysis: given W, h, ε_r → ε_eff, Z₀
# --------------------------------------------------------------------------


def _eff_permittivity(w_h: float, er: float) -> float:
    """Effective dielectric constant for a microstrip line (Hammerstad-Jensen)."""
    a = (
        1.0
        + (1.0 / 49.0) * math.log((w_h**4 + (w_h / 52.0) ** 2) / (w_h**4 + 0.432))
        + (1.0 / 18.7) * math.log(1.0 + (w_h / 18.1) ** 3)
    )
    b = 0.564 * ((er - 0.9) / (er + 3.0)) ** 0.053
    return float((er + 1) / 2 + (er - 1) / 2 * (1 + 10 / w_h) ** (-a * b))


def _impedance(w_h: float, er_eff: float) -> float:
    """Characteristic impedance from W/h ratio + effective permittivity."""
    eta0 = 376.730313668  # free-space impedance, Ω
    if w_h <= 1.0:
        z = (eta0 / (2 * math.pi * math.sqrt(er_eff))) * math.log(8.0 / w_h + w_h / 4.0)
    else:
        z = (eta0 / math.sqrt(er_eff)) / (w_h + 1.393 + 0.667 * math.log(w_h + 1.444))
    return z


def _dielectric_loss_db_per_m(
    er: float,
    er_eff: float,
    tan_d: float,
    freq_hz: float,
) -> float:
    """Dielectric loss for a microstrip line (Pozar §3.8.1, Eq 3.197).

    α_d (Np/m) = (k₀ · ε_r · (ε_eff − 1) · tan_d) / (2·√ε_eff · (ε_r − 1))

    Returns dB/m. Multiply by 8.686 to convert Np → dB.
    """
    if er <= 1 or tan_d <= 0:
        return 0.0
    k0 = 2.0 * math.pi * freq_hz / C0
    alpha_np = (k0 * er * (er_eff - 1) * tan_d) / (2.0 * math.sqrt(er_eff) * (er - 1))
    return 8.686 * alpha_np  # dB/m


def _conductor_loss_db_per_m(
    z0_ohm: float,
    width_mm: float,
    freq_hz: float,
    sigma_s_per_m: float = 5.8e7,
) -> float:
    """Conductor loss for a microstrip line (Pozar §3.8.1, simplified).

    α_c (Np/m) = R_s / (Z₀ · w) where R_s = √(π·f·μ₀/σ) is the surface
    resistance. Default σ = 5.8 × 10⁷ S/m (copper).

    Returns dB/m. Approximation neglects current crowding at trace edges
    (Hammerstad-Jensen K_a correction); accurate to ~1 dB/m up to a few GHz.
    """
    if width_mm <= 0 or z0_ohm <= 0 or sigma_s_per_m <= 0:
        return 0.0
    mu0 = 4.0 * math.pi * 1e-7
    rs = math.sqrt(math.pi * freq_hz * mu0 / sigma_s_per_m)  # Ω/sq
    alpha_np = rs / (z0_ohm * (width_mm * 1e-3))
    return 8.686 * alpha_np  # dB/m


def analyze_microstrip(
    width_mm: float,
    substrate: Substrate,
    freq_hz: float = 1e9,
    *,
    sigma_s_per_m: float = 5.8e7,
) -> dict[str, float]:
    """Compute Z₀, ε_eff, wavelength, and per-mm loss for a given microstrip width.

    Returns a dict with:
    - ``z0_ohm`` — characteristic impedance
    - ``er_eff`` — effective dielectric constant
    - ``w_h_ratio`` — width-to-substrate-height ratio
    - ``wavelength_eff_mm`` — effective guided wavelength
    - ``phase_velocity_m_s`` — phase velocity
    - ``alpha_d_db_per_mm`` — dielectric loss (uses substrate ``tan_d``)
    - ``alpha_c_db_per_mm`` — conductor loss (default copper σ = 5.8e7 S/m)
    - ``alpha_total_db_per_mm`` — sum

    ``sigma_s_per_m`` defaults to copper. Override for gold (4.1e7),
    aluminium (3.5e7), or measured plating.
    """
    if width_mm <= 0:
        raise ValueError(f"width_mm must be positive, got {width_mm}")
    w_h = width_mm / substrate.h_mm
    er_eff = _eff_permittivity(w_h, substrate.er)
    z0 = _impedance(w_h, er_eff)
    wavelength_eff_m = C0 / (freq_hz * math.sqrt(er_eff))
    alpha_d_db_per_m = _dielectric_loss_db_per_m(substrate.er, er_eff, substrate.tan_d, freq_hz)
    alpha_c_db_per_m = _conductor_loss_db_per_m(z0, width_mm, freq_hz, sigma_s_per_m)
    return {
        "z0_ohm": z0,
        "er_eff": er_eff,
        "w_h_ratio": w_h,
        "wavelength_eff_mm": wavelength_eff_m * 1000.0,
        "phase_velocity_m_s": C0 / math.sqrt(er_eff),
        "alpha_d_db_per_mm": alpha_d_db_per_m / 1000.0,
        "alpha_c_db_per_mm": alpha_c_db_per_m / 1000.0,
        "alpha_total_db_per_mm": (alpha_d_db_per_m + alpha_c_db_per_m) / 1000.0,
    }


# --------------------------------------------------------------------------
# Synthesis: given Z₀, ε_r, h → W
# --------------------------------------------------------------------------


def synthesize_width(z0_ohm: float, substrate: Substrate) -> float:
    """Closed-form W/h for a target Z₀ (Hammerstad-Jensen synthesis).

    Two formulas: the "low W/h" form (valid for W/h ≤ 2, i.e. higher Z₀)
    and the "high W/h" form (valid for W/h ≥ 2, i.e. lower Z₀). Pick
    whichever lands a result inside its own validity range. The low-W/h
    form can produce negative or unphysical numbers at very low Z₀, so
    we always validate before accepting it.

    Returns the trace width in mm.
    """
    if z0_ohm <= 0:
        raise ValueError(f"z0_ohm must be positive, got {z0_ohm}")
    er = substrate.er

    # Low-W/h formula (valid when result is ≤ 2)
    a = (z0_ohm / 60) * math.sqrt((er + 1) / 2) + ((er - 1) / (er + 1)) * (0.23 + 0.11 / er)
    denom_lo = math.exp(2 * a) - 2
    w_h_lo = 8 * math.exp(a) / denom_lo if denom_lo > 0 else float("inf")

    # High-W/h formula (valid when result is ≥ 2)
    b = 377 * math.pi / (2 * z0_ohm * math.sqrt(er))
    w_h_hi = (2 / math.pi) * (
        b - 1 - math.log(2 * b - 1) + ((er - 1) / (2 * er)) * (math.log(b - 1) + 0.39 - 0.61 / er)
    )

    # Prefer whichever lands inside its own validity range.
    if 0 < w_h_lo <= 2:
        w_h = w_h_lo
    elif w_h_hi >= 2:
        w_h = w_h_hi
    else:
        # Borderline: use the larger positive of the two
        w_h = max(w_h_lo if w_h_lo > 0 else 0, w_h_hi if w_h_hi > 0 else 0)
        if w_h <= 0:
            raise ValueError(
                f"Hammerstad-Jensen synthesis failed for Z0={z0_ohm} Ω, εr={er}; "
                f"result outside model validity range."
            )
    return w_h * substrate.h_mm


def synthesize_microstrip_line(
    z0_ohm: float,
    electrical_length_deg: float,
    freq_hz: float,
    substrate: Substrate,
) -> MicrostripLine:
    """Full synthesis: compute width and length for a target line.

    - ``z0_ohm``: target characteristic impedance
    - ``electrical_length_deg``: phase length at ``freq_hz`` (e.g. 90 for
      a quarter-wave transformer)
    - ``freq_hz``: design center frequency
    - ``substrate``: dielectric stackup
    """
    if not 0 <= electrical_length_deg <= 720:
        raise ValueError(f"electrical_length_deg out of range: {electrical_length_deg}")
    width_mm = synthesize_width(z0_ohm, substrate)
    a = analyze_microstrip(width_mm, substrate, freq_hz)
    # length = electrical_length / 360 * λ_eff
    length_mm = (electrical_length_deg / 360.0) * a["wavelength_eff_mm"]
    return MicrostripLine(
        z0=a["z0_ohm"],
        width_mm=width_mm,
        length_mm=length_mm,
        eff_permittivity=a["er_eff"],
        electrical_length_deg=electrical_length_deg,
        freq_hz=freq_hz,
        substrate=substrate,
        wavelength_eff_mm=a["wavelength_eff_mm"],
        metadata={
            "w_h_ratio": a["w_h_ratio"],
            "z0_target": z0_ohm,
            "z0_error_pct": (a["z0_ohm"] - z0_ohm) / z0_ohm * 100,
        },
    )
