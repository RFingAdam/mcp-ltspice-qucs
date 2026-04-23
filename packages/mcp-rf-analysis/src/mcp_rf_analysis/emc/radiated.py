"""Radiated-emissions estimation via small-loop / short-dipole approximations.

A current loop of area A (m²) carrying current I (A) at frequency f (Hz)
radiates a far-field magnetic field. At distance r:

    H = (π · I · A · f²) / (c² · r)

Convert to E-field via E = η₀ · H ≈ 377 · H. Convert to dBµV/m for
comparison with FCC Part 15.209 / CISPR 22 limits.

This is the standard "small-loop antenna" approximation, valid when
loop dimensions << wavelength. Useful for catching obvious radiated-EMI
problems in early layout review.
"""

from __future__ import annotations

import math

C0 = 299_792_458.0
ETA0 = 376.730313668


def predict_radiated_emissions_loop(
    *,
    current_a: float,
    loop_area_cm2: float,
    freq_hz: float,
    measurement_distance_m: float = 3.0,
) -> dict[str, float]:
    """Estimate radiated E-field from a current-carrying loop.

    Inputs:
    - ``current_a``: harmonic current amplitude (A peak)
    - ``loop_area_cm2``: total loop area (e.g. clock trace + return path)
    - ``measurement_distance_m``: 3m or 10m per the standard

    Returns: E-field in dBµV/m at the measurement distance.
    """
    if current_a <= 0 or loop_area_cm2 <= 0 or freq_hz <= 0:
        raise ValueError("All inputs must be positive")
    a_m2 = loop_area_cm2 * 1e-4
    h_a_per_m = (math.pi * current_a * a_m2 * freq_hz**2) / (C0**2 * measurement_distance_m)
    e_v_per_m = ETA0 * h_a_per_m
    e_uv_per_m = e_v_per_m * 1e6
    e_dbuv_per_m = 20 * math.log10(max(e_uv_per_m, 1e-12))
    return {
        "current_a": current_a,
        "loop_area_cm2": loop_area_cm2,
        "freq_hz": freq_hz,
        "distance_m": measurement_distance_m,
        "h_a_per_m": h_a_per_m,
        "e_v_per_m": e_v_per_m,
        "e_dbuv_per_m": e_dbuv_per_m,
    }


# FCC Part 15.109 Class B radiated limits (US, residential), dBµV/m at 3m
# Above 1 GHz the limit is on average power, not field strength — this
# table covers up to 1 GHz only.
_FCC_15_109_B_AT_3M: list[tuple[float, float]] = [
    (30e6, 100.0),  # converted from 100 µV/m to dBµV/m: 20·log10(100) = 40 dBµV/m
    (88e6, 100.0),
    (216e6, 150.0),
    (960e6, 200.0),
]


# Real values per §15.109(a):
#   30-88 MHz   : 100 µV/m at 3m → 40.0 dBµV/m
#   88-216 MHz  : 150 µV/m at 3m → 43.5 dBµV/m
#   216-960 MHz : 200 µV/m at 3m → 46.0 dBµV/m
#   >960 MHz    : 500 µV/m at 3m → 54.0 dBµV/m
_FCC_15_109_B_LIMITS_DBUV_M_3M: list[tuple[float, float]] = [
    (30e6, 40.0),
    (88e6, 40.0),
    (88.001e6, 43.5),
    (216e6, 43.5),
    (216.001e6, 46.0),
    (960e6, 46.0),
    (960.001e6, 54.0),
    (40e9, 54.0),
]


def fcc_part15_radiated_limit_at(
    freq_hz: float,
    *,
    distance_m: float = 3.0,
) -> float:
    """Return the FCC §15.109(a) Class B radiated limit at the given freq,
    referred to the measurement distance.

    Below 30 MHz this returns NaN (use Part 15.209 magnetic-field limits).
    Above 40 GHz also NaN.
    """
    if freq_hz < 30e6 or freq_hz > 40e9:
        return float("nan")

    # Look up table
    from itertools import pairwise

    for (f1, l1), (f2, _l2) in pairwise(_FCC_15_109_B_LIMITS_DBUV_M_3M):
        if f1 <= freq_hz <= f2:
            limit_at_3m = l1
            break
    else:
        limit_at_3m = _FCC_15_109_B_LIMITS_DBUV_M_3M[-1][1]

    # Convert from 3m to measurement distance via inverse-square law (far field)
    # E ∝ 1/r → adjustment in dB = 20·log10(3/distance_m)
    distance_correction_db = 20 * math.log10(3.0 / distance_m)
    return limit_at_3m + distance_correction_db
