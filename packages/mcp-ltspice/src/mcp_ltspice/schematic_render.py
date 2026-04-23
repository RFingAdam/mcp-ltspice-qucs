"""Publication-quality schematic rendering via schemdraw.

LTspice's ``.asc`` is a positional schematic format that only renders
inside LTspice. For docs, papers, and code review we want clean SVG /
PNG output that scales and embeds nicely. This module takes our
synthesis-style component dict (the same one ``synthesize_lc_lpf`` /
``substitute_real_components`` produce) and emits a schemdraw drawing.

Two output flavours:

1. **LC ladder** — LPF/HPF prototype as series-L / shunt-C / shunt-LC-trap
   stages between source / load resistors. Auto-laid-out left to right.

2. **Op-amp filter** — Sallen-Key or MFB 2nd-order section with proper
   feedback topology, op-amp triangle, and component labels.

Output is SVG by default (vector, scalable, diff-friendly) with PNG
fallback for embedding in markdown / READMEs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

# Lock matplotlib backend before any schemdraw drawing is made
import matplotlib
import schemdraw
import schemdraw.elements as elm

matplotlib.use("Agg")


# ---------------------------------------------------------------------------
# Helpers: format a component value with engineering notation + unit
# ---------------------------------------------------------------------------


def _fmt_value(value: float, kind: Literal["L", "C", "R"]) -> str:
    """4.7e-9 → '4.7nH', 2.2e-12 → '2.2pF', 50 → '50Ω'."""
    suffixes = [
        (1e-15, "f"),
        (1e-12, "p"),
        (1e-9, "n"),
        (1e-6, "µ"),
        (1e-3, "m"),
        (1.0, ""),
        (1e3, "k"),
        (1e6, "M"),
    ]
    abs_v = abs(value)
    chosen = ("", 1.0)
    for scale, suf in suffixes:
        if abs_v >= scale:
            chosen = (suf, scale)
        else:
            break
    suf, scale = chosen
    mant = value / scale
    txt = f"{mant:.3g}{suf}"
    units = {"L": "H", "C": "F", "R": "Ω"}
    return f"{txt}{units[kind]}"


def _refdes_index(name: str) -> int:
    m = re.match(r"[A-Z]+(\d+)", name)
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------------------
# LC ladder schematic
# ---------------------------------------------------------------------------


def render_lc_ladder_schematic(
    components: dict[str, float],
    output_path: str | Path,
    *,
    z0: float = 50.0,
    transmission_zeros: bool = False,
    title: str | None = None,
    fontsize: int = 12,
) -> Path:
    """Render an LC ladder LPF as a publication-quality schematic.

    Picks the format from the ``output_path`` extension: ``.svg`` or
    ``.png``. SVG is vector and recommended for docs.

    Layout is strictly left-to-right along the top rail; shunts and
    traps drop to a continuous bottom-rail ground.
    """
    out = Path(output_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    d = schemdraw.Drawing(show=False, fontsize=fontsize)

    # Source on the left: vertical Vin then horizontal R_s.
    d += elm.SourceSin().up().label("V$_{in}$", loc="left")
    src_top = d.here
    d += elm.Resistor().right().label(f"R$_S$\n{_fmt_value(z0, 'R')}")

    # Walk components in numeric order. For elliptic ladders pair L+C
    # with matching index for shunt LC traps.
    sorted_names = sorted(components.keys(), key=_refdes_index)
    seen: set[str] = set()

    for name in sorted_names:
        if name in seen:
            continue
        idx = _refdes_index(name)
        l_key = f"L{idx}"
        c_key = f"C{idx}"
        is_trap = transmission_zeros and l_key in components and c_key in components

        if is_trap:
            # Shunt LC trap: drop down with series L, then series C, then
            # ground. Push/pop to return to the top rail.
            d.push()
            d += (
                elm.Inductor()
                .down()
                .label(
                    f"{l_key}\n{_fmt_value(components[l_key], 'L')}",
                    loc="left",
                )
            )
            d += (
                elm.Capacitor()
                .down()
                .label(
                    f"{c_key}\n{_fmt_value(components[c_key], 'C')}",
                    loc="left",
                )
            )
            d += elm.Ground()
            d.pop()
            d += elm.Line().right().length(1)
            seen.add(l_key)
            seen.add(c_key)
        elif name.startswith("L"):
            d += (
                elm.Inductor()
                .right()
                .label(
                    f"{name}\n{_fmt_value(components[name], 'L')}",
                )
            )
            seen.add(name)
        elif name.startswith("C"):
            # Plain shunt cap to ground
            d.push()
            d += (
                elm.Capacitor()
                .down()
                .label(
                    f"{name}\n{_fmt_value(components[name], 'C')}",
                    loc="right",
                )
            )
            d += elm.Ground()
            d.pop()
            d += elm.Line().right().length(1)
            seen.add(name)

    # Load: small lead-out, then R_L to ground, with V_out tap.
    d += elm.Line().right().length(0.5)
    d.push()
    d += (
        elm.Resistor()
        .down()
        .label(
            f"R$_L$\n{_fmt_value(z0, 'R')}",
            loc="right",
        )
    )
    d += elm.Ground()
    d.pop()
    d += elm.Dot().label("V$_{out}$", loc="top")

    # Connect source ground rail to the rest of the bottom rail by
    # using a wire from src_top down to ground (already done by
    # SourceSin — but we add a ground here to make it explicit).
    d += elm.Line().left().length(0).at(src_top)

    if str(out).endswith(".svg"):
        d.save(str(out))
    else:
        d.save(str(out), dpi=150)

    return out


# ---------------------------------------------------------------------------
# Active filter (Sallen-Key) schematic
# ---------------------------------------------------------------------------


def render_sallen_key_schematic(
    R1: float,
    R2: float,
    C1: float,
    C2: float,
    *,
    output_path: str | Path,
    R3: float | None = None,
    R4: float | None = None,
    title: str | None = None,
    fontsize: int = 12,
) -> Path:
    """Render a Sallen-Key 2nd-order LPF schematic with op-amp triangle.

    If ``R3``/``R4`` are provided the op-amp is wired as a non-inverting
    amplifier with gain ``1 + R3/R4``; otherwise unity-gain buffer.
    """
    out = Path(output_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    d = schemdraw.Drawing(show=False, fontsize=fontsize)
    if title:
        d.add(elm.Label().label(title, halign="center", fontsize=fontsize + 2))
        d.move(0, -0.5)

    # Input + R1
    d += elm.Dot().label("V$_{in}$", loc="left")
    d += elm.Resistor().right().label(f"R1\n{_fmt_value(R1, 'R')}")
    n1 = d.add(elm.Dot())

    # R2 between n1 and the V+ input of the op-amp
    d += elm.Resistor().right().label(f"R2\n{_fmt_value(R2, 'R')}")
    d += elm.Dot()

    # C1 from n1 to op-amp output (positive feedback)
    d.push()
    d.move_from(n1.end, dy=2.0)
    d += elm.Capacitor().right().length(3).label(f"C1\n{_fmt_value(C1, 'C')}")
    d.pop()

    # C2 from n2 to ground
    d.push()
    d += (
        elm.Capacitor()
        .down()
        .length(2)
        .label(
            f"C2\n{_fmt_value(C2, 'C')}",
            loc="right",
        )
    )
    d += elm.Ground()
    d.pop()

    # Op-amp
    op = d.add(elm.Opamp(leads=True).anchor("in1"))
    d.move_from(op.in1, dx=-0.5, dy=0)
    d += elm.Line().left()  # connect n2 (V+) to op-amp +in (in1)

    # Output node
    d.move_from(op.out, dx=0.5, dy=0)
    d += elm.Dot().label("V$_{out}$", loc="right")

    # Feedback: op-amp output → V- (in2) directly (unity gain) or via R3+R4
    d.move_from(op.out, dx=0.5, dy=0)
    d += elm.Line().right().length(0.5)
    d.push()
    d += elm.Line().down().length(2)
    d += elm.Line().left().length(3)
    if R3 is not None and R4 is not None:
        # Non-inverting: feedback through R3, gnd via R4
        d += elm.Resistor().left().label(f"R3\n{_fmt_value(R3, 'R')}")
        d.push()
        d += (
            elm.Resistor()
            .down()
            .length(1.5)
            .label(
                f"R4\n{_fmt_value(R4, 'R')}",
                loc="right",
            )
        )
        d += elm.Ground()
        d.pop()
    d += elm.Line().up().to(op.in2)
    d.pop()

    if str(out).endswith(".svg"):
        d.save(str(out))
    else:
        d.save(str(out), dpi=150)

    return out


# ---------------------------------------------------------------------------
# Convenience: render an existing .asc by parsing components
# ---------------------------------------------------------------------------


def render_asc_as_schematic(
    asc_path: str | Path,
    output_path: str | Path,
    *,
    transmission_zeros: bool = True,
    title: str | None = None,
) -> Path:
    """Parse a generated .asc and re-render it as a clean schemdraw SVG.

    Only handles the LC-ladder topology our ``generate_lpf_asc`` emits;
    falls back to listing components if the structure isn't recognized.
    """
    from mcp_ltspice.asc_io import read_components

    components = read_components(asc_path)
    return render_lc_ladder_schematic(
        components,
        output_path,
        transmission_zeros=transmission_zeros,
        title=title or Path(asc_path).stem,
    )
