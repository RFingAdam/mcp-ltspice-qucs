#!/usr/bin/env python3
"""Render every LTspice .asc under examples/ to a clean schemdraw SVG + PNG.

Walks the examples tree, parses each .asc, and emits a sibling .svg
(vector, scalable) plus a .png (for inline markdown). Skips .asc files
that don't match the LC-ladder topology our generator emits.
"""

from __future__ import annotations

import sys
from pathlib import Path

from mcp_ltspice.schematic_render import render_asc_as_schematic

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


def _looks_elliptic(asc_path: Path) -> bool:
    """Heuristic: an elliptic ladder has paired Lk + Ck for some even k."""
    text = asc_path.read_text(encoding="utf-8", errors="replace")
    inames = set()
    for line in text.splitlines():
        if line.startswith("SYMATTR InstName "):
            inames.add(line.split(maxsplit=2)[2].strip())
    return any(f"L{n}" in inames and f"C{n}" in inames for n in (2, 4, 6, 8, 10))


def main() -> int:
    asc_files = sorted(EXAMPLES.glob("**/*.asc"))
    if not asc_files:
        print(f"No .asc files found under {EXAMPLES}")
        return 0

    print(f"Found {len(asc_files)} schematic files")
    n_ok = 0
    n_fail = 0

    for asc in asc_files:
        is_ellip = _looks_elliptic(asc)
        title = f"{asc.parent.name} / {asc.stem}"
        for ext in (".svg", ".png"):
            out = asc.with_suffix(f".schematic{ext}")
            try:
                render_asc_as_schematic(
                    asc,
                    out,
                    transmission_zeros=is_ellip,
                    title=title,
                )
                print(f"  ✓ {asc.relative_to(ROOT)} -> {out.name}")
                n_ok += 1
            except Exception as e:
                print(f"  ✗ {asc.relative_to(ROOT)} -> {out.name}: {e}")
                n_fail += 1

    print(f"\nDone: {n_ok} rendered, {n_fail} failed.")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
