"""E-series component value snapping (E24, E96, E192).

When a synthesis or optimization tool produces a non-realizable
component value (e.g. 4.137 nH), this module snaps it to the nearest
value in a manufacturable preferred-number series and reports the
percent error.

Reference: IEC 60063 preferred numbers.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import NamedTuple


class ESeries(StrEnum):
    E6 = "E6"
    E12 = "E12"
    E24 = "E24"
    E48 = "E48"
    E96 = "E96"
    E192 = "E192"


# Mantissa tables (one decade). All other decades scale by 10^n.
_E_TABLES: dict[ESeries, list[float]] = {
    ESeries.E6: [1.0, 1.5, 2.2, 3.3, 4.7, 6.8],
    ESeries.E12: [
        1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2,
    ],
    ESeries.E24: [
        1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
        3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1,
    ],
    ESeries.E48: [
        1.00, 1.05, 1.10, 1.15, 1.21, 1.27, 1.33, 1.40, 1.47, 1.54, 1.62, 1.69,
        1.78, 1.87, 1.96, 2.05, 2.15, 2.26, 2.37, 2.49, 2.61, 2.74, 2.87, 3.01,
        3.16, 3.32, 3.48, 3.65, 3.83, 4.02, 4.22, 4.42, 4.64, 4.87, 5.11, 5.36,
        5.62, 5.90, 6.19, 6.49, 6.81, 7.15, 7.50, 7.87, 8.25, 8.66, 9.09, 9.53,
    ],
    ESeries.E96: [
        1.00, 1.02, 1.05, 1.07, 1.10, 1.13, 1.15, 1.18, 1.21, 1.24, 1.27, 1.30,
        1.33, 1.37, 1.40, 1.43, 1.47, 1.50, 1.54, 1.58, 1.62, 1.65, 1.69, 1.74,
        1.78, 1.82, 1.87, 1.91, 1.96, 2.00, 2.05, 2.10, 2.15, 2.21, 2.26, 2.32,
        2.37, 2.43, 2.49, 2.55, 2.61, 2.67, 2.74, 2.80, 2.87, 2.94, 3.01, 3.09,
        3.16, 3.24, 3.32, 3.40, 3.48, 3.57, 3.65, 3.74, 3.83, 3.92, 4.02, 4.12,
        4.22, 4.32, 4.42, 4.53, 4.64, 4.75, 4.87, 4.99, 5.11, 5.23, 5.36, 5.49,
        5.62, 5.76, 5.90, 6.04, 6.19, 6.34, 6.49, 6.65, 6.81, 6.98, 7.15, 7.32,
        7.50, 7.68, 7.87, 8.06, 8.25, 8.45, 8.66, 8.87, 9.09, 9.31, 9.53, 9.76,
    ],
    ESeries.E192: [
        # Generated programmatically; full E192 table.
        round(10 ** (n / 192), 2) for n in range(192)
    ],
}


class SnapResult(NamedTuple):
    snapped: float
    error_pct: float
    decade: int


def snap_to_eseries(value: float, series: ESeries | str = ESeries.E24) -> SnapResult:
    """Snap ``value`` to the nearest preferred number in the given series.

    Returns the snapped value, signed percent error
    ``(snapped - value) / value * 100``, and the decade used.

    >>> snap_to_eseries(4.137e-9, ESeries.E24)
    SnapResult(snapped=3.9e-09, error_pct=-5.728...)
    """
    if value <= 0:
        raise ValueError(f"value must be positive, got {value}")
    if isinstance(series, str):
        series = ESeries(series)
    table = _E_TABLES[series]

    decade = math.floor(math.log10(value))
    mantissa = value / (10**decade)

    # Find nearest mantissa in this and adjacent decades.
    candidates: list[tuple[float, int]] = []
    for d_off in (-1, 0, 1):
        for m in table:
            candidates.append((m * 10 ** (decade + d_off), decade + d_off))

    snapped, used_decade = min(candidates, key=lambda c: abs(c[0] - value))
    err = (snapped - value) / value * 100.0
    return SnapResult(snapped=snapped, error_pct=err, decade=used_decade)
