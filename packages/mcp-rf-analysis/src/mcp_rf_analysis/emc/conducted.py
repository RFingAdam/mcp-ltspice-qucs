"""Conducted-emissions estimation.

A LISN (Line Impedance Stabilization Network) presents a known
50 Ω + 50 µH series + 0.1 µF impedance to the AC mains. The voltage
measured across the LISN's 50 Ω resistor is the EMI receiver's input.

This module:
- Converts a current spectrum (e.g. from your SMPS sim) to a LISN
  voltage spectrum
- Compares against CISPR 22 / 32 / FCC Part 15B limits
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal


@dataclass
class LISNModel:
    """Standard CISPR 16-1-2 LISN: 50 µH || 5 Ω in series with 0.1 µF
    in parallel with 50 Ω measurement port."""

    L_uh: float = 50.0
    R_par_ohm: float = 5.0
    C_uf: float = 0.1
    Z_meas_ohm: float = 50.0


def lisn_impedance(freq_hz: float, lisn: LISNModel | None = None) -> complex:
    """Compute the LISN impedance Z(f) seen by the EUT power line at the
    measurement port.

    For frequencies above 150 kHz (CISPR start), the LISN looks
    essentially like 50 Ω in parallel with the measurement-port 50 Ω
    → 25 Ω. Below 150 kHz the inductor dominates.
    """
    if lisn is None:
        lisn = LISNModel()
    omega = 2 * math.pi * freq_hz
    z_l = 1j * omega * lisn.L_uh * 1e-6
    z_c = 1.0 / (1j * omega * lisn.C_uf * 1e-6)
    z_par_branch = z_l + lisn.R_par_ohm
    # Parallel of (z_par_branch) || (Z_c in series with Z_meas)
    z_meas_branch = z_c + lisn.Z_meas_ohm
    return (z_par_branch * z_meas_branch) / (z_par_branch + z_meas_branch)


# CISPR 22 / 32 Class B limits (residential), conducted, dBµV (quasi-peak)
# Per CISPR 32:2015 Table A.1:
#   0.15-0.50 MHz : 66 → 56 dBµV log-linear (decreasing)
#   0.50-5.00 MHz : 56 dBµV (constant)
#   5.00-30.0 MHz : 60 dBµV (constant — step up at 5 MHz)
# The (5e6, 56) → (5.001e6, 60) pair encodes the step discontinuity
# so log-linear interpolation does not silently average across it.
_CISPR22_CLASS_B_QP = [
    (150e3, 66.0),
    (500e3, 56.0),
    (5e6, 56.0),
    (5.001e6, 60.0),
    (30e6, 60.0),
]


# FCC Part 15B Class B (US), dBµV — same shape as CISPR Class B
_FCC_15B_CLASS_B = [
    (150e3, 66.0),
    (500e3, 56.0),
    (5e6, 56.0),
    (5.001e6, 60.0),
    (30e6, 60.0),
]


def cispr_limit_at(
    freq_hz: float,
    *,
    standard: Literal["cispr22_b", "cispr22_a", "fcc15b_b"] = "cispr22_b",
) -> float:
    """Return the conducted-emissions limit (dBµV, quasi-peak) at a
    given frequency for the requested standard / class."""
    if standard == "cispr22_b" or standard == "fcc15b_b":
        table = _CISPR22_CLASS_B_QP
    elif standard == "cispr22_a":
        # Class A is +10 dB above Class B
        return cispr_limit_at(freq_hz, standard="cispr22_b") + 10
    else:
        raise ValueError(f"Unknown standard: {standard}")

    if freq_hz < table[0][0] or freq_hz > table[-1][0]:
        raise ValueError(
            f"Frequency {freq_hz / 1e6:.2f} MHz outside conducted "
            f"emissions range ({table[0][0] / 1e6:.2f}-{table[-1][0] / 1e6:.0f} MHz)."
        )

    # Log-linear interpolation between table points
    from itertools import pairwise

    for (f1, l1), (f2, l2) in pairwise(table):
        if f1 <= freq_hz <= f2:
            if f1 == f2:
                return l1
            ratio = math.log(freq_hz / f1) / math.log(f2 / f1)
            return l1 + ratio * (l2 - l1)
    return table[-1][1]


def predict_conducted_emissions(
    line_current_spectrum: list[tuple[float, float]],
    *,
    standard: Literal["cispr22_b", "cispr22_a", "fcc15b_b"] = "cispr22_b",
    margin_db: float = 6.0,
) -> dict[str, Any]:
    """Convert a (freq_hz, current_a_rms) spectrum to a LISN voltage
    spectrum and compare against the conducted-emissions limit.

    Returns per-frequency: measured (dBµV), limit (dBµV), margin.
    Negative margin = noncompliant. ``margin_db`` is the design buffer
    (e.g. 6 dB below limit recommended for production).
    """
    lisn = LISNModel()
    freqs: list[float] = []
    measured: list[float] = []
    limits: list[float] = []
    margins: list[float] = []
    statuses: list[str] = []

    for f_hz, i_rms in line_current_spectrum:
        if f_hz < 150e3 or f_hz > 30e6:
            continue
        z = lisn_impedance(f_hz, lisn)
        v_rms = abs(z) * i_rms
        v_dbuv = 20 * math.log10(max(v_rms / 1e-6, 1e-9))
        limit = cispr_limit_at(f_hz, standard=standard)
        target = limit - margin_db
        margin = target - v_dbuv
        freqs.append(f_hz)
        measured.append(v_dbuv)
        limits.append(limit)
        margins.append(margin)
        statuses.append("pass" if margin >= 0 else "fail")

    n_fail = sum(1 for s in statuses if s == "fail")
    return {
        "standard": standard,
        "margin_db": margin_db,
        "freq_hz": freqs,
        "measured_dbuv": measured,
        "limit_dbuv": limits,
        "margin_db_per_freq": margins,
        "status": statuses,
        "overall": "pass" if n_fail == 0 else "fail",
        "n_violations": n_fail,
    }
