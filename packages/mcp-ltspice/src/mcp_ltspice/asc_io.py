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

import codecs
import contextlib
import math
import os
import re
from dataclasses import dataclass
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


# Schematic geometry.
#
# LTspice has no netlist section in a .asc: connectivity is purely
# positional. Two pins are the same node when they share a coordinate, and a
# wire joins its two endpoints. Emitting symbols without wires — as this
# module did until the pins below were worked out — produces a schematic that
# looks right and netlists as a pile of disconnected ``NC_*`` nodes.
#
# Pin offsets are from the stock symbol library (``lib/sym/*.asy``); the R90
# mapping ``(dx, dy) -> (-dy, dx)`` was verified against LTspice 26.0.2 by
# netlisting a probe schematic and checking which net labels attached.
#
# Placing a two-terminal symbol therefore means solving for the SYMBOL
# anchor that puts its pins where we want them, which is what the three
# ``_place_*`` helpers below do.
_GAP = 64  #: horizontal wire run between adjacent elements
_SERIES_SPAN = 80  #: legacy alias; prefer series_span(kind)
_CAP_SPAN = 64  #: pin-to-pin length of a vertical cap
_VERT_SPAN = 80  #: pin-to-pin length of a vertical res/ind/source


#: Pin-to-pin length and first-pin y-offset per symbol kind, from the stock
#: symbol library. A cap's pins are (16,0)/(16,64); res and ind are
#: (16,16)/(16,96). Getting this wrong places a series capacitor 16 units off
#: its wire, which reads as a plausible schematic and netlists as an open
#: circuit.
_SYMBOL_GEOMETRY = {
    "res": (80, 16),
    "ind": (80, 16),
    "voltage": (80, 16),
    "cap": (64, 0),
}


def series_span(kind: str) -> int:
    """Pin-to-pin length of ``kind`` once rotated horizontal."""
    try:
        return _SYMBOL_GEOMETRY[kind][0]
    except KeyError:
        raise ValueError(f"No pin geometry known for symbol {kind!r}") from None


def _place_series(kind: str, x_left_pin: int, y: int) -> list[str]:
    """Horizontal two-terminal symbol with pins on ``(x_left_pin, y)`` and
    ``(x_left_pin + series_span(kind), y)``.

    Solves the R90 mapping ``(dx, dy) -> (-dy, dx)`` for the anchor.
    """
    span, first_dy = _SYMBOL_GEOMETRY[kind]
    return _symbol(kind, x_left_pin + first_dy + span, y - 16, "R90")


def _place_vertical(kind: str, x: int, y_top_pin: int) -> list[str]:
    """Vertical res/ind/voltage with its top pin on ``(x, y_top_pin)``."""
    # Top pin sits 16 below the anchor for all three symbols; res/ind are
    # also inset 16 in x, while the voltage source's pins are on its axis.
    anchor_dx = 0 if kind == "voltage" else 16
    return _symbol(kind, x - anchor_dx, y_top_pin - 16, "R0")


def _place_cap(x: int, y_top_pin: int) -> list[str]:
    """Vertical cap with its top pin on ``(x, y_top_pin)``."""
    return _symbol("cap", x - 16, y_top_pin, "R0")


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

    # Everything hangs off a single horizontal signal rail at y_axis; shunt
    # elements drop below it to ground flags.
    x_left = 96
    y_axis = 144

    # Now walk the components in numeric order
    def _idx(name: str) -> int:
        m = re.match(r"[LC](\d+)", name)
        if not m:
            raise ValueError(f"Bad refdes: {name}")
        return int(m.group(1))

    sorted_names = sorted(components.keys(), key=_idx)
    seen: set[str] = set()
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

    # Walk left to right along the rail, wiring each element to the last.
    # `x` always holds the coordinate of the live node on the rail.
    x = x_left
    flags: list[str] = []

    # Source: V1 (AC 1) with its negative terminal on ground.
    lines.extend(_place_vertical("voltage", x, y_axis))
    lines.append(_attr("InstName", "V1"))
    lines.append(_attr("Value", "AC 1 0"))
    flags.append(_flag(x, y_axis + _VERT_SPAN, "0"))

    # Source resistor Rs1 from the source node to port 1.
    lines.append(_wire(x, y_axis, x + _GAP, y_axis))
    x += _GAP
    lines.extend(_place_series("res", x, y_axis))
    lines.append(_attr("InstName", "Rs1"))
    lines.append(_attr("Value", to_ltspice_value(z0)))
    x += series_span("res")
    flags.append(_flag(x, y_axis, "p1"))

    for elt_kind, params in elements:
        k = int(params["k"])
        # Wire the previous node across to where this element starts.
        lines.append(_wire(x, y_axis, x + _GAP, y_axis))
        x += _GAP

        if elt_kind == "series_l":
            lines.extend(_place_series("ind", x, y_axis))
            lines.append(_attr("InstName", f"L{k}"))
            lines.append(_attr("Value", to_ltspice_value(params["L"])))
            x += series_span("ind")
        elif elt_kind == "shunt_c":
            # Shunt cap hangs off the rail; the node does not advance.
            lines.extend(_place_cap(x, y_axis))
            lines.append(_attr("InstName", f"C{k}"))
            lines.append(_attr("Value", to_ltspice_value(params["C"])))
            flags.append(_flag(x, y_axis + _CAP_SPAN, "0"))
        elif elt_kind == "trap":
            # Series LC to ground: inductor from the rail, cap beneath it.
            lines.extend(_place_vertical("ind", x, y_axis))
            lines.append(_attr("InstName", f"L{k}"))
            lines.append(_attr("Value", to_ltspice_value(params["L"])))
            lines.extend(_place_cap(x, y_axis + _VERT_SPAN))
            lines.append(_attr("InstName", f"C{k}"))
            lines.append(_attr("Value", to_ltspice_value(params["C"])))
            flags.append(_flag(x, y_axis + _VERT_SPAN + _CAP_SPAN, "0"))

    # Load: RL1 from port 2 to ground.
    lines.append(_wire(x, y_axis, x + _GAP, y_axis))
    x += _GAP
    lines.extend(_place_vertical("res", x, y_axis))
    lines.append(_attr("InstName", "RL1"))
    lines.append(_attr("Value", to_ltspice_value(z0)))
    flags.append(_flag(x, y_axis, "p2"))
    flags.append(_flag(x, y_axis + _VERT_SPAN, "0"))

    # AC analysis directive
    lines.append(
        f"TEXT {x_left} {y_axis + 320} Left 2 !.ac dec {npoints_per_decade} "
        f"{f_start_hz} {f_stop_hz}"
    )
    lines.extend(flags)

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


# --------------------------------------------------------------------------
# Read / modify existing .asc
# --------------------------------------------------------------------------


class AscDecodeError(ValueError):
    """An ``.asc`` could not be decoded as any encoding LTspice writes."""


#: Byte-order marks LTspice files carry, longest first so UTF-16 wins over a
#: prefix match. LTspice XVII writes UTF-16LE with BOM; LTspice 24+ writes
#: UTF-8. Both use CRLF.
_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


@dataclass(frozen=True)
class AscText:
    """Decoded ``.asc`` contents plus what it takes to write it back.

    ``update_component`` edits schematics the user owns, so it must not
    quietly re-encode them. Round-tripping an LTspice XVII file through
    UTF-8/LF would rewrite every byte of a file whose contents were never
    successfully read in the first place.
    """

    text: str
    encoding: str
    newline: str

    def render(self, lines: list[str]) -> bytes:
        body = self.newline.join(lines) + self.newline
        return body.encode(self.encoding)


def _decode_asc(asc_path: str | Path) -> AscText:
    """Read an ``.asc``, detecting its encoding and line endings.

    Raises :class:`AscDecodeError` rather than returning mojibake: the old
    ``errors="replace"`` path turned an unreadable UTF-16 file into a
    successful-looking parse of zero components.
    """
    raw = Path(asc_path).read_bytes()

    encoding: str | None = None
    for bom, enc in _BOMS:
        if raw.startswith(bom):
            encoding = enc
            break

    candidates = [encoding] if encoding else ["utf-8", "utf-16", "cp1252"]
    text: str | None = None
    for enc in candidates:
        assert enc is not None
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        encoding = enc
        break

    if text is None or encoding is None:
        raise AscDecodeError(
            f"Could not decode {asc_path} as any encoding LTspice writes "
            f"(tried: {', '.join(c for c in candidates if c)}). "
            "LTspice XVII writes UTF-16LE with a BOM; LTspice 24+ writes UTF-8."
        )

    # Detect line endings from the first break, defaulting to the platform
    # convention LTspice itself uses.
    if "\r\n" in text:
        newline = "\r\n"
    elif "\n" in text:
        newline = "\n"
    else:
        newline = "\r\n"

    return AscText(text=text, encoding=encoding, newline=newline)


def read_asc_text(asc_path: str | Path) -> AscText:
    """Public wrapper around encoding detection, for callers that re-write."""
    return _decode_asc(asc_path)


def read_components(asc_path: str | Path) -> dict[str, float]:
    """Return a {refdes: value} dict for L* and C* components in the .asc.

    Raises :class:`AscDecodeError` if the file cannot be decoded, so an
    unreadable schematic is distinguishable from one that genuinely holds
    no components.
    """
    text = _decode_asc(asc_path).text
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
    schematic. The explicitly requested in-place edit is committed atomically."""
    p = Path(asc_path).expanduser().resolve()
    doc = _decode_asc(p)
    out_lines: list[str] = []
    cur_refdes: str | None = None
    found = False
    for line in doc.text.splitlines():
        if line.startswith("SYMBOL "):
            cur_refdes = None
            out_lines.append(line)
        elif line.startswith("SYMATTR InstName "):
            cur_refdes = line.split(maxsplit=2)[2].strip()
            out_lines.append(line)
        elif line.startswith("SYMATTR Value ") and cur_refdes == refdes:
            out_lines.append(f"SYMATTR Value {to_ltspice_value(new_value)}")
            found = True
        else:
            out_lines.append(line)

    if not found:
        raise KeyError(
            f"No component {refdes!r} in {p}. Writing the file back unchanged "
            "would look like a successful edit; refusing instead."
        )

    # Stage and atomically replace so interruption cannot truncate the source.
    staging = p.with_name(f".{p.name}.update.tmp")
    try:
        staging.write_bytes(doc.render(out_lines))
        os.replace(staging, p)
    finally:
        if staging.exists():
            staging.unlink()
    return p


def generate_port2_excitation_asc(
    asc_path: str | Path,
    output_path: str | Path,
    *,
    z0: float,
) -> Path:
    """Create the second matched-port excitation for two-sweep extraction.

    The input must use the package fixture: ``V1`` and ``Rs1`` drive ``p1``
    while ``RL1`` terminates ``p2``. The generated copy sets ``V1`` to AC 0,
    retains ``Rs1`` as the port-1 termination, and inserts ``V2 AC 1`` below
    ``RL1`` so that resistor becomes the port-2 source impedance.

    Ambiguous or shared ground geometry is rejected instead of rewired.
    """
    if z0 <= 0:
        raise ValueError(f"z0 must be > 0; got {z0}")

    # Local import avoids an import cycle: asc_netlist uses the value and text
    # helpers in this module.
    from mcp_ltspice.asc_netlist import build_nodes, parse_asc

    source = Path(asc_path).expanduser().resolve()
    target = Path(output_path).expanduser().resolve()
    doc = _decode_asc(source)
    schematic = parse_asc(source)
    by_name = {symbol.inst: symbol for symbol in schematic.symbols if symbol.inst}
    if len(by_name) != len([symbol for symbol in schematic.symbols if symbol.inst]):
        raise ValueError("Duplicate component instance names make the port fixture ambiguous")
    if "V2" in by_name:
        raise ValueError(
            "The source schematic already contains V2; cannot insert port-2 excitation"
        )

    required = {"V1": "voltage", "Rs1": "res", "RL1": "res"}
    for name, kind in required.items():
        symbol = by_name.get(name)
        if symbol is None or symbol.kind != kind:
            raise ValueError(f"Two-sweep extraction requires {name} to be a {kind} symbol")

    v1 = by_name["V1"]
    rs1 = by_name["Rs1"]
    rl1 = by_name["RL1"]
    if v1.value is None or rs1.value is None or rl1.value is None:
        raise ValueError("V1, Rs1, and RL1 must each have a Value attribute")
    if not re.match(r"(?i)^AC\s+1(?:\.0*)?(?:\s+0(?:\.0*)?)?$", v1.value.strip()):
        raise ValueError(f"V1 must be `AC 1` for the declared fixture; got {v1.value!r}")
    for name, symbol in (("Rs1", rs1), ("RL1", rl1)):
        try:
            resistance = from_ltspice_value(symbol.value or "")
        except ValueError as exc:
            raise ValueError(f"{name} must have a numeric impedance value") from exc
        if not math.isclose(resistance, z0, rel_tol=1e-9, abs_tol=0.0):
            raise ValueError(f"{name}={resistance:g} ohm does not match declared z0={z0:g} ohm")

    nodes = build_nodes(schematic)
    v1_nodes = [nodes[pin] for pin in v1.pins()]
    rs1_nodes = [nodes[pin] for pin in rs1.pins()]
    rl1_nodes = [nodes[pin] for pin in rl1.pins()]
    if set(rl1_nodes) != {"p2", "0"}:
        raise ValueError(f"RL1 must connect only p2 to ground; got {rl1_nodes}")
    if "0" not in v1_nodes or "p1" not in rs1_nodes:
        raise ValueError("V1/Rs1 fixture must drive a net labelled p1 from ground")
    v1_signal = next(node for node in v1_nodes if node != "0")
    if v1_signal not in rs1_nodes:
        raise ValueError("V1 and Rs1 are not connected in series")

    rl1_ground_pin = rl1.pins()[rl1_nodes.index("0")]
    ground_flags = [
        flag
        for flag in schematic.flags
        if flag[:2] == rl1_ground_pin and flag[2].lower() in {"0", "gnd"}
    ]
    if len(ground_flags) != 1:
        raise ValueError("RL1 ground pin must have exactly one local ground flag")
    for symbol in schematic.symbols:
        if symbol is not rl1 and rl1_ground_pin in symbol.pins():
            raise ValueError("RL1 ground pin is shared with another symbol; refusing to rewire it")
    if any(
        (x1, y1) == rl1_ground_pin
        or (x2, y2) == rl1_ground_pin
        or (x1 == x2 == rl1_ground_pin[0] and min(y1, y2) <= rl1_ground_pin[1] <= max(y1, y2))
        or (y1 == y2 == rl1_ground_pin[1] and min(x1, x2) <= rl1_ground_pin[0] <= max(x1, x2))
        for x1, y1, x2, y2 in schematic.wires
    ):
        raise ValueError("RL1 ground pin touches wiring; refusing an ambiguous port-2 rewrite")

    v2_lines = [
        *_place_vertical("voltage", rl1_ground_pin[0], rl1_ground_pin[1]),
        _attr("InstName", "V2"),
        _attr("Value", "AC 1 0"),
        _flag(rl1_ground_pin[0], rl1_ground_pin[1] + _VERT_SPAN, "0"),
    ]
    output: list[str] = []
    current_inst: str | None = None
    inserted = False
    for line in doc.text.splitlines():
        stripped = line.strip()
        if stripped.startswith("SYMBOL "):
            current_inst = None
        elif stripped.startswith("SYMATTR InstName "):
            current_inst = stripped.split(maxsplit=2)[2]
        if stripped.startswith("SYMATTR Value ") and current_inst == "V1":
            line = "SYMATTR Value AC 0 0"
        if stripped == f"FLAG {rl1_ground_pin[0]} {rl1_ground_pin[1]} 0":
            continue
        if not inserted and stripped.startswith("TEXT "):
            output.extend(v2_lines)
            inserted = True
        output.append(line)
    if not inserted:
        output.extend(v2_lines)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(doc.render(output))
    return target
