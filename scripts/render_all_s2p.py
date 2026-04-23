#!/usr/bin/env python3
"""Render a Bode plot (S21 + S11) for every .s2p file under examples/.

Picks frequency markers heuristically: HaLow-related files get HaLow
coex markers; everything else gets generic "fc / 2fc / 3fc" markers
based on each file's measured -3 dB point.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from mcp_ltspice.render import render_response
from rf_mcp_common.touchstone import read_touchstone

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"

# HaLow-specific markers (used when the .s2p is in halow_lpf/)
HALOW_MARKERS = [
    (928e6, "passband edge"),
    (1575e6, "GPS L1"),
    (1840e6, "B3 DL 2H"),
    (1960e6, "B25 DL"),
    (2440e6, "ISM 2.4G"),
    (2745e6, "NA 3H"),
]


def _find_minus_3db(s2p: Path) -> float | None:
    """Locate the -3 dB cutoff frequency from the file's S21 trace."""
    try:
        net = read_touchstone(s2p)
        s21_db = 20 * np.log10(np.maximum(np.abs(net.s[:, 1, 0]), 1e-12))
        # Find first crossing below -3 dB starting from low freq
        below = np.where(s21_db < -3.0)[0]
        if below.size == 0:
            return None
        return float(net.f[below[0]])
    except Exception:
        return None


def _generic_markers(s2p: Path) -> list[tuple[float, str]]:
    fc = _find_minus_3db(s2p)
    if fc is None:
        return []
    return [
        (fc, "fc (-3 dB)"),
        (2 * fc, "2x fc"),
        (3 * fc, "3x fc"),
    ]


def _markers_for(s2p: Path) -> list[tuple[float, str]]:
    if "halow_lpf" in s2p.parts:
        return HALOW_MARKERS
    return _generic_markers(s2p)


def main() -> int:
    s2p_files = sorted(EXAMPLES.glob("**/*.s2p"))
    if not s2p_files:
        print(f"No .s2p files found under {EXAMPLES}")
        return 0

    print(f"Found {len(s2p_files)} S-parameter files")
    n_rendered = 0
    n_skipped = 0
    n_failed = 0

    for s2p in s2p_files:
        png = s2p.with_suffix(".png")
        # If the script's current name matches the canonical "response.png"
        # for this folder we still re-render — keep them in sync.
        try:
            markers = _markers_for(s2p)
            title = f"{s2p.parent.name} / {s2p.stem}"
            render_response(
                s2p,
                png,
                markers=markers,
                title=title,
                show_s11=True,
            )
            print(f"  ✓ {s2p.relative_to(ROOT)} -> {png.name}")
            n_rendered += 1
        except Exception as e:
            print(f"  ✗ {s2p.relative_to(ROOT)}: {e}")
            n_failed += 1

    print(f"\nDone: {n_rendered} rendered, {n_skipped} skipped, {n_failed} failed.")
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
