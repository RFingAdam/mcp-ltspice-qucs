"""LTspice .asc schematic I/O. (No security concerns - stdlib import.)

Two complementary capabilities:

1. **Generate** — produce a ``.asc`` from a synthesized component dict
   plus port-mapping metadata, using a small set of known LPF templates.

2. **Read / modify** — wrap ``spicelib.AscEditor`` for component-value
   updates that preserve schematic structure.

Generated schematics follow the convention used by the runner:
- Port 1 driven by ``V1 AC 1 0`` through ``Rs1 = z0``
- Port 2 terminated by ``RL1 = z0`` to ground
- All component values use SI multiplier suffixes (``n`` = nano, ``p``
  = pico, etc.) so they read cleanly inside LTspice itself.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import Literal

# SI scientific notation -> LTspice "engineering" suffix.
_LTSPICE_SUFFIX = [
    (1e-15, "f"),
    (1e-12, "p"),
    (1e-9, "n"),
    (1e-6, "u"),
    (1e-3, "m"),
    (1.0, ""),
    (1e3, "k"),
    (1e6, "Meg"),
    (1e9, "G"),
]


def to_ltspice_value(value: float) -> str:
    """Convert a float to an LTspice/SPICE-friendly engineering string."""
    if value == 0:
        return "0"
    abs_v = abs(value)
    chosen = ("", 1.0)
    for scale, suffix in _LTSPICE_SUFFIX:
        if abs_v >= scale:
            chosen = (suffix, scale)
        else:
            break
    suffix, scale = chosen
    mantissa = value / scale
    if abs(mantissa - round(mantissa)) < 1e-9:
        return f"{round(mantissa)}{suffix}"
    return f"{mantissa:.4g}{suffix}"


def from_ltspice_value(text: str) -> float:
    """Parse an LTspice value string back to a float."""
    text = text.strip()
    m = re.match(r"^\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*([a-zA-Z]*)\s*$", text)
    if not m:
        raise ValueError(f"Cannot parse LTspice value: {text!r}")
    mantissa = float(m.group(1))
    suffix = m.group(2)
    suffix_norm = suffix.lower() if suffix.lower() != "meg" else "meg"
    table = {
        "f": 1e-15,
        "p": 1e-12,
        "n": 1e-9,
        "u": 1e-6,
        "µ": 1e-6,
        "m": 1e-3,
        "": 1.0,
        "k": 1e3,
        "meg": 1e6,
        "g": 1e9,
        "t": 1e12,
    }
    # Handle "Meg" specifically (case-insensitive)
    if suffix.lower() == "meg":
        return mantissa * 1e6
    if suffix_norm not in table:
        raise ValueError(f"Unknown LTspice suffix: {suffix!r}")
    return mantissa * table[suffix_norm]


# --------------------------------------------------------------------------
# Schematic generation: text-based emitters for a few canonical topologies
# --------------------------------------------------------------------------


Topology = Literal["lpf_t_butterworth_chebyshev", "lpf_t_elliptic"]


def _asc_header() -> list[str]:
    return [
        "Version 4",
        "SHEET 1 1280 720",
    ]


def _wire(x1: int, y1: int, x2: int, y2: int) -> str:
    return f"WIRE {x1} {y1} {x2} {y2}"


def _flag(x: int, y: int, name: str) -> str:
    return f"FLAG {x} {y} {name}"


def _symbol(kind: str, x: int, y: int, rot: str = "R0") -> list[str]:
    return [f"SYMBOL {kind} {x} {y} {rot}"]


def _attr(name: str, value: str) -> str:
    return f"SYMATTR {name} {value}"


def generate_lpf_asc(
    components: dict[str, float],
    output_path: str | Path,
    *,
    topology: Topology = "lpf_t_butterworth_chebyshev",
    z0: float = 50.0,
    f_start_hz: float = 1e6,
    f_stop_hz: float = 5e9,
    npoints_per_decade: int = 200,
) -> Path:
    """Emit an LTspice ``.asc`` for an LPF.

    The generated schematic is sufficient to run ``LTspice -b`` for an
    AC sweep that the :func:`extract.extract_sparams_from_raw` reader
    expects (port 1 = V1 + Rs1, port 2 = RL1 to ground).
    """
    out = Path(output_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = _asc_header()

    # Place source on the left and load on the right. LTspice uses 16-pixel
    # grid; positions are arbitrary but must be consistent so wires connect.
    # We hand-place the symbols on a uniform x-axis stride.
    x_left = 96
    y_axis = 144
    x_load = x_left + 192 * (len(components) + 1)

    # Source: voltage source with AC 1
    lines.extend(_symbol("voltage", x_left - 64, y_axis, "R0"))
    lines.append(_attr("InstName", "V1"))
    lines.append(_attr("Value", "AC 1 0"))

    # Source resistor Rs1 between V1 and node "n1"
    lines.extend(_symbol("res", x_left - 16, y_axis - 96, "R90"))
    lines.append(_attr("InstName", "Rs1"))
    lines.append(_attr("Value", to_ltspice_value(z0)))

    # Now walk the components in numeric order
    def _idx(name: str) -> int:
        m = re.match(r"[LC](\d+)", name)
        if not m:
            raise ValueError(f"Bad refdes: {name}")
        return int(m.group(1))

    sorted_names = sorted(components.keys(), key=_idx)
    seen: set[str] = set()
    pos_x = x_left
    node_idx = 1
    elements: list[tuple[str, dict[str, float]]] = []

    if topology == "lpf_t_elliptic":
        # Pair (Lk, Ck) for matching k as shunt LC traps
        for name in sorted_names:
            if name in seen:
                continue
            k = _idx(name)
            l_key = f"L{k}"
            c_key = f"C{k}"
            if l_key in components and c_key in components:
                elements.append(("trap", {"L": components[l_key], "C": components[c_key], "k": k}))
                seen.add(l_key)
                seen.add(c_key)
            elif l_key in components:
                elements.append(("series_l", {"L": components[l_key], "k": k}))
                seen.add(l_key)
            else:
                elements.append(("shunt_c", {"C": components[c_key], "k": k}))
                seen.add(c_key)
    else:
        for name in sorted_names:
            if name.startswith("L"):
                elements.append(("series_l", {"L": components[name], "k": _idx(name)}))
            else:
                elements.append(("shunt_c", {"C": components[name], "k": _idx(name)}))

    # Walk through elements left-to-right
    for elt_kind, params in elements:
        k = int(params["k"])
        if elt_kind == "series_l":
            lines.extend(_symbol("ind", pos_x, y_axis - 96, "R90"))
            lines.append(_attr("InstName", f"L{k}"))
            lines.append(_attr("Value", to_ltspice_value(params["L"])))
            pos_x += 192
        elif elt_kind == "shunt_c":
            lines.extend(_symbol("cap", pos_x - 64, y_axis - 64, "R0"))
            lines.append(_attr("InstName", f"C{k}"))
            lines.append(_attr("Value", to_ltspice_value(params["C"])))
        elif elt_kind == "trap":
            # Inductor on top, cap on bottom, both shunt to ground
            lines.extend(_symbol("ind", pos_x - 64, y_axis - 64, "R0"))
            lines.append(_attr("InstName", f"L{k}"))
            lines.append(_attr("Value", to_ltspice_value(params["L"])))
            lines.extend(_symbol("cap", pos_x - 64, y_axis + 16, "R0"))
            lines.append(_attr("InstName", f"C{k}"))
            lines.append(_attr("Value", to_ltspice_value(params["C"])))
        node_idx += 1

    # Load: RL1 to ground at the rightmost node
    lines.extend(_symbol("res", x_load, y_axis - 96, "R0"))
    lines.append(_attr("InstName", "RL1"))
    lines.append(_attr("Value", to_ltspice_value(z0)))

    # AC analysis directive
    lines.append(
        f"TEXT {x_left} {y_axis + 192} Left 2 !.ac dec {npoints_per_decade} "
        f"{f_start_hz} {f_stop_hz}"
    )
    # Net labels for the runner's S-parameter extraction
    lines.append(_flag(x_left, y_axis, "p1"))
    lines.append(_flag(x_load + 80, y_axis, "p2"))
    lines.append(_flag(x_load + 80, y_axis + 96, "0"))  # ground

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


# --------------------------------------------------------------------------
# Read / modify existing .asc
# --------------------------------------------------------------------------


def read_components(asc_path: str | Path) -> dict[str, float]:
    """Return a {refdes: value} dict for L* and C* components in the .asc."""
    text = Path(asc_path).read_text(encoding="utf-8", errors="replace")
    out: dict[str, float] = {}
    refdes: str | None = None
    for line in text.splitlines():
        if line.startswith("SYMBOL "):
            refdes = None
        elif line.startswith("SYMATTR InstName "):
            refdes = line.split(maxsplit=2)[2].strip()
        elif line.startswith("SYMATTR Value ") and refdes:
            value_str = line.split(maxsplit=2)[2].strip()
            if refdes[0] in ("L", "C"):
                with contextlib.suppress(ValueError):
                    out[refdes] = from_ltspice_value(value_str)
    return out


def update_component(asc_path: str | Path, refdes: str, new_value: float) -> Path:
    """Set the Value of a single L/C component, preserving the rest of the
    schematic. Writes in-place and returns the path."""
    p = Path(asc_path)
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    out_lines: list[str] = []
    cur_refdes: str | None = None
    for line in lines:
        if line.startswith("SYMBOL "):
            cur_refdes = None
            out_lines.append(line)
        elif line.startswith("SYMATTR InstName "):
            cur_refdes = line.split(maxsplit=2)[2].strip()
            out_lines.append(line)
        elif line.startswith("SYMATTR Value ") and cur_refdes == refdes:
            out_lines.append(f"SYMATTR Value {to_ltspice_value(new_value)}")
        else:
            out_lines.append(line)
    p.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return p
