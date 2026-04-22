"""Unit conversion helpers — Hz internal, dB / dBm display.

The cross-tool contract is: **frequencies are always Hz on the wire**.
Display-friendly units (MHz, GHz) belong in tool responses for human
readability, never in arguments or stored data.
"""

from __future__ import annotations

import math
from enum import StrEnum


class FreqUnit(StrEnum):
    """Recognized frequency unit suffixes for parsing user input."""

    HZ = "Hz"
    KHZ = "kHz"
    MHZ = "MHz"
    GHZ = "GHz"


_FREQ_MULT: dict[FreqUnit, float] = {
    FreqUnit.HZ: 1.0,
    FreqUnit.KHZ: 1e3,
    FreqUnit.MHZ: 1e6,
    FreqUnit.GHZ: 1e9,
}


def hz(value: float, unit: FreqUnit | str = FreqUnit.HZ) -> float:
    """Convert a frequency value with unit to Hz."""
    if isinstance(unit, str):
        unit = FreqUnit(unit)
    return value * _FREQ_MULT[unit]


def db(linear: float) -> float:
    """Linear power ratio → dB (10 log10)."""
    if linear <= 0:
        return float("-inf")
    return 10.0 * math.log10(linear)


def lin(db_value: float) -> float:
    """dB → linear power ratio."""
    return 10.0 ** (db_value / 10.0)


def w_to_dbm(power_w: float) -> float:
    """Power in watts → dBm."""
    if power_w <= 0:
        return float("-inf")
    return 10.0 * math.log10(power_w * 1000.0)


def dbm_to_w(dbm: float) -> float:
    """dBm → power in watts."""
    return 10.0 ** (dbm / 10.0) / 1000.0
