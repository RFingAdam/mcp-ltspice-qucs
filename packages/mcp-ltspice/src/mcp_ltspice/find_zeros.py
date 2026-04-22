"""Detect transmission zeros (S21 notches) in a Touchstone file."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from rf_mcp_common.touchstone import read_touchstone
from scipy.signal import find_peaks


def find_transmission_zeros(
    s2p_path: str | Path,
    *,
    min_depth_db: float = 20.0,
    f_min_hz: float | None = None,
    f_max_hz: float | None = None,
) -> list[dict[str, float]]:
    """Locate notches in |S21| where the depth (negative peak) is at
    least ``min_depth_db`` below the local plateau.

    Returns a list of {freq_hz, depth_db, q_factor} sorted by frequency.
    """
    net = read_touchstone(s2p_path)
    f = net.f
    s21_db = 20 * np.log10(np.maximum(np.abs(net.s[:, 1, 0]), 1e-12))
    mask = np.ones_like(f, dtype=bool)
    if f_min_hz is not None:
        mask &= f >= f_min_hz
    if f_max_hz is not None:
        mask &= f <= f_max_hz
    f_w = f[mask]
    s21_w = s21_db[mask]

    # Notches are negative peaks → invert the signal and find peaks
    inverted = -s21_w
    peaks, _ = find_peaks(inverted, prominence=min_depth_db)

    out: list[dict[str, float]] = []
    for idx in peaks:
        f0 = float(f_w[idx])
        depth = float(-s21_w[idx])
        # 3-dB Q estimate: bandwidth from notch where depth drops by 3 dB
        threshold = -s21_w[idx] - 3.0
        # Find left/right indices where -s21 < threshold
        left = idx
        while left > 0 and -s21_w[left] > threshold:
            left -= 1
        right = idx
        while right < len(s21_w) - 1 and -s21_w[right] > threshold:
            right += 1
        bw = f_w[right] - f_w[left]
        q = f0 / bw if bw > 0 else float("inf")
        out.append({"freq_hz": f0, "depth_db": depth, "q_factor": q})
    return out
