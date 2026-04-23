"""Control-loop compensator design (Type-II / Type-III) and stability.

For voltage-mode buck converters the standard compensator is Type-III
(zero-pole-zero), and for current-mode is Type-II (single zero, single
pole). These functions return the RC values around the error amp +
report the resulting crossover frequency and phase margin.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class CompensatorDesign:
    topology: str  # "type2" | "type3"
    crossover_hz: float
    phase_margin_deg: float
    components: dict[str, float]
    transfer_function_hz: list[float]
    transfer_function_db: list[float]
    transfer_function_phase_deg: list[float]
    notes: list[str]


def type2_compensator(
    *,
    crossover_hz: float,
    plant_zero_hz: float,
    plant_pole_hz: float,
    phase_boost_deg: float = 60.0,
    rfb_kohm: float = 10.0,
) -> CompensatorDesign:
    """Type-II compensator (1 zero + 1 pole + integrator) for current-mode SMPS.

    Type-II provides up to 90° of phase boost; ``phase_boost_deg`` ≤ 90.
    Standard "K-factor" method:
        K  = tan( (phase_boost/2) + 45° )
        f_zero = f_xover / K
        f_pole = f_xover · K

    The compensator is built around an op-amp with feedback:
        R_fb (top of divider, also feedback resistor) → fixed
        R_z, C_z = zero-creating
        C_p = pole-creating

        f_zero = 1 / (2π · R_z · C_z)
        f_pole = 1 / (2π · R_z · (C_z·C_p)/(C_z + C_p))
    """
    if not 0 < phase_boost_deg < 90:
        raise ValueError(f"phase_boost_deg must be in (0, 90), got {phase_boost_deg}")

    # K-factor for placing the zero and pole symmetrically around f_xover
    k = math.tan(math.radians(phase_boost_deg / 2 + 45))
    f_zero = crossover_hz / k
    f_pole = crossover_hz * k

    rfb = rfb_kohm * 1e3
    # For a Type-II inverting compensator, the gain at midband is the
    # plant inverse — we'll use rfb as a normalisation (user can scale)
    cz = 1.0 / (2 * math.pi * rfb * f_zero)
    cp = cz / (k**2 - 1) if k != 1 else cz / 1e-6

    notes = []
    if phase_boost_deg > 80:
        notes.append(
            "Phase boost > 80° is at Type-II's practical limit. Consider Type-III for higher boost."
        )

    f = np.geomspace(crossover_hz / 1000, crossover_hz * 1000, 401)
    s = 1j * 2 * math.pi * f
    omega_z = 2 * math.pi * f_zero
    omega_p = 2 * math.pi * f_pole
    # Transfer: K_int · (1 + s/ω_z) / (s · (1 + s/ω_p))
    h = (1 + s / omega_z) / (s * (1 + s / omega_p))
    h_db = 20 * np.log10(np.abs(h))
    # Normalise so |H(j·2π·crossover)| = 0 dB
    h_at_xover = float(np.interp(crossover_hz, f, h_db))
    h_db = h_db - h_at_xover

    h_phase = np.degrees(np.angle(h))

    return CompensatorDesign(
        topology="type2",
        crossover_hz=crossover_hz,
        phase_margin_deg=90 - phase_boost_deg,  # rough — assumes plant adds 0 here
        components={"R_fb": rfb, "C_z": cz, "C_p": cp},
        transfer_function_hz=f.tolist(),
        transfer_function_db=h_db.tolist(),
        transfer_function_phase_deg=h_phase.tolist(),
        notes=notes,
    )


def compute_phase_margin(
    open_loop_freq_hz: list[float],
    open_loop_mag_db: list[float],
    open_loop_phase_deg: list[float],
) -> dict[str, float]:
    """Find crossover (where |H|=0 dB) and the phase there → phase margin.

    Returns:
        crossover_hz: frequency where |H| crosses 0 dB
        phase_at_crossover_deg: open-loop phase at that frequency
        phase_margin_deg: 180° + phase  (negative phase → positive margin)
    """
    f = np.asarray(open_loop_freq_hz)
    mag = np.asarray(open_loop_mag_db)
    phase = np.asarray(open_loop_phase_deg)

    # Find the first crossing where mag goes from positive to negative
    sign_change = np.where(np.diff(np.sign(mag)) < 0)[0]
    if len(sign_change) == 0:
        return {
            "crossover_hz": float("nan"),
            "phase_at_crossover_deg": float("nan"),
            "phase_margin_deg": float("nan"),
            "stable": False,
        }
    idx = int(sign_change[0])
    # Linear interpolate to find exact crossover
    f_cross = float(np.interp(0, [mag[idx + 1], mag[idx]], [f[idx + 1], f[idx]]))
    phase_cross = float(np.interp(f_cross, f, phase))
    pm = 180.0 + phase_cross
    return {
        "crossover_hz": f_cross,
        "phase_at_crossover_deg": phase_cross,
        "phase_margin_deg": pm,
        "stable": pm > 0,
        "well_compensated": pm > 45,
    }
