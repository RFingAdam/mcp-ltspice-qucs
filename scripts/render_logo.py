"""Regenerate the project logos from a real filter response.

The curve in every logo is the actual |S21| (and faint |S11|) of an
order-5 elliptic low-pass filter synthesized by this codebase — the
product draws its own logo. Run from the repo root:

    uv run python scripts/render_logo.py

Outputs: assets/logo-mark.svg, assets/logo.svg, assets/logo-banner.svg
(and mirrors the first two into docs/assets/).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np

from mcp_ltspice.extract import components_dict_to_elements, ladder_sparams_from_components
from mcp_ltspice.synthesis.lc_filter import synthesize_lc_lpf

ROOT = Path(__file__).resolve().parent.parent

# Palette — the established navy/blue/teal brand.
BG_TOP, BG_BOT = "#0B1220", "#101B31"
GRID, GRID_SOFT = "#1C2A47", "#15213A"
CURVE_A, CURVE_B = "#3AA1FF", "#5FD7C8"
S11 = "#7286A8"
NOTCH = "#5FD7C8"
INK, INK_DIM = "#E8EDF6", "#8FA1BE"

MONO = "ui-monospace,'SF Mono','Cascadia Code',Menlo,Consolas,monospace"


def response(npts: int = 480) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[float]]:
    """|S21| and |S11| in dB of a real order-5 elliptic LPF, log-f axis."""
    d = synthesize_lc_lpf("elliptic", order=5, cutoff_hz=1e9, ripple_db=0.15, stopband_atten_db=42)
    els = components_dict_to_elements(d.components, topology="series_first", kind="lowpass")
    f = np.geomspace(0.08e9, 12e9, npts)
    s = ladder_sparams_from_components(els, f, z0=50.0)
    s21 = 20 * np.log10(np.maximum(np.abs(s[:, 1, 0]), 1e-9))
    s11 = 20 * np.log10(np.maximum(np.abs(s[:, 0, 0]), 1e-9))
    return f, s21, s11, sorted(d.transmission_zeros_hz)


def curve_paths(
    x0: float, y0: float, w: float, h: float, db_floor: float = -68.0, db_ceil: float = 6.0
) -> tuple[str, str, list[float]]:
    """Map the response into an (x0, y0, w, h) box; returns S21 path,
    S11 path, and the x pixel positions of the transmission zeros."""
    f, s21, s11, zeros = response()
    lf = np.log10(f)
    xs = x0 + (lf - lf[0]) / (lf[-1] - lf[0]) * w

    def ys(db: np.ndarray) -> np.ndarray:
        return y0 + (db_ceil - np.clip(db, db_floor, db_ceil)) / (db_ceil - db_floor) * h

    def path(db: np.ndarray) -> str:
        y = ys(db)
        return "M" + "L".join(f"{x:.1f},{v:.1f}" for x, v in zip(xs, y, strict=True))

    zx = [float(x0 + (np.log10(z) - lf[0]) / (lf[-1] - lf[0]) * w) for z in zeros]
    return path(s21), path(s11), zx


def defs(prefix: str) -> str:
    return f"""  <defs>
    <linearGradient id="{prefix}bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{BG_TOP}"/><stop offset="100%" stop-color="{BG_BOT}"/>
    </linearGradient>
    <linearGradient id="{prefix}cv" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="{CURVE_A}"/><stop offset="45%" stop-color="{CURVE_A}"/>
      <stop offset="70%" stop-color="{CURVE_B}"/><stop offset="100%" stop-color="{CURVE_B}"/>
    </linearGradient>
  </defs>"""


def plot_group(x0: float, y0: float, w: float, h: float, prefix: str, grid_cols: int = 6) -> str:
    s21, s11, zx = curve_paths(x0, y0, w, h)
    gx = "".join(
        f'<line x1="{x0 + w * i / grid_cols:.1f}" y1="{y0}" x2="{x0 + w * i / grid_cols:.1f}" '
        f'y2="{y0 + h}" stroke="{GRID_SOFT}" stroke-width="1"/>'
        for i in range(1, grid_cols)
    )
    gy = "".join(
        f'<line x1="{x0}" y1="{y0 + h * i / 4:.1f}" x2="{x0 + w}" y2="{y0 + h * i / 4:.1f}" '
        f'stroke="{GRID_SOFT}" stroke-width="1"/>'
        for i in range(1, 4)
    )
    notches = "".join(
        f'<line x1="{x:.1f}" y1="{y0 + 6:.1f}" x2="{x:.1f}" y2="{y0 + h:.1f}" '
        f'stroke="{NOTCH}" stroke-width="1" stroke-dasharray="2 4" opacity="0.55"/>'
        for x in zx
    )
    frame = (
        f'<rect x="{x0}" y="{y0}" width="{w}" height="{h}" fill="none" '
        f'stroke="{GRID}" stroke-width="1.5"/>'
    )
    return (
        f"<g>{gx}{gy}{notches}{frame}"
        f'<path d="{s11}" fill="none" stroke="{S11}" stroke-width="1.6" opacity="0.45"/>'
        f'<path d="{s21}" fill="none" stroke="url(#{prefix}cv)" stroke-width="3" '
        f'stroke-linejoin="round" stroke-linecap="round"/></g>'
    )


def write(path: Path, svg: str) -> None:
    path.write_text(svg + "\n", encoding="utf-8")
    print(f"wrote {path} ({len(svg) / 1024:.1f} KB)")


def logo_mark() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" role="img"
     aria-label="mcp-ltspice-qucs mark: elliptic low-pass response with transmission-zero notches">
  <title>mcp-ltspice-qucs</title>
{defs("m")}
  <rect x="0" y="0" width="128" height="128" rx="24" fill="url(#mbg)"/>
{plot_group(16, 26, 96, 76, "m", grid_cols=4)}
</svg>"""


def logo_card() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 240" role="img"
     aria-label="mcp-ltspice-qucs: a real elliptic low-pass response drawn by the toolkit itself">
  <title>mcp-ltspice-qucs</title>
{defs("c")}
  <rect x="0" y="0" width="480" height="240" rx="20" fill="url(#cbg)"/>
{plot_group(40, 36, 400, 130, "c")}
  <text x="40" y="200" font-family="{MONO}" font-size="21" font-weight="600"
        fill="{INK}" letter-spacing="0.5">mcp-ltspice-qucs</text>
  <text x="40" y="221" font-family="{MONO}" font-size="11.5" fill="{INK_DIM}">order-5 elliptic |S21| — drawn by its own tools</text>
</svg>"""


def logo_banner() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 320" role="img"
     aria-label="mcp-ltspice-qucs — RF filter synthesis, simulation, and coexistence analysis via LTspice, Qucs-S, ngspice, and Xyce">
  <title>mcp-ltspice-qucs</title>
{defs("b")}
  <rect x="0" y="0" width="1280" height="320" rx="0" fill="url(#bbg)"/>
{plot_group(700, 52, 520, 216, "b", grid_cols=8)}
  <text x="64" y="130" font-family="{MONO}" font-size="46" font-weight="700"
        fill="{INK}" letter-spacing="0.5">mcp-ltspice-qucs</text>
  <text x="64" y="172" font-family="{MONO}" font-size="19" fill="{INK_DIM}">RF filter synthesis, simulation, and coexistence</text>
  <text x="64" y="198" font-family="{MONO}" font-size="19" fill="{INK_DIM}">analysis — LTspice · Qucs-S · ngspice · Xyce</text>
  <text x="64" y="252" font-family="{MONO}" font-size="14" fill="{CURVE_A}">110 MCP tools · 4 simulator backends · validated against all of them</text>
</svg>"""


def main() -> None:
    assets = ROOT / "assets"
    write(assets / "logo-mark.svg", logo_mark())
    write(assets / "logo.svg", logo_card())
    write(assets / "logo-banner.svg", logo_banner())
    for name in ("logo-mark.svg", "logo.svg"):
        shutil.copy(assets / name, ROOT / "docs" / "assets" / name)
        print(f"mirrored docs/assets/{name}")


if __name__ == "__main__":
    main()
