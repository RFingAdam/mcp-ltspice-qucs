"""Netlist an LTspice ``.asc`` by its geometry, the way LTspice does.

The previous ngspice netlister inferred each element's *position* from its
refdes letter: a lone ``C{n}`` was assumed to be a shunt capacitor, a lone
``L{n}`` a series inductor. That is only ever true of a lowpass ladder. Hand
it a highpass (series C, shunt L) and it silently emitted the dual network —
ngspice ran happily and returned S-parameters for a circuit nobody had
designed (issue #32). ``z0`` was hardcoded to 50 Ω on top of that, so a 75 Ω
design was simulated at 50 Ω.

Reading the schematic instead removes the guesswork. LTspice has no netlist
section in a ``.asc``: connectivity *is* geometry. Two pins are the same node
when they share a coordinate, a wire joins its endpoints, and a pin landing
on a wire is on that wire's node. Implementing those rules directly gives a
netlister that is correct for any topology, including schematics this package
did not generate, and lets the port impedance be read from the terminating
resistors actually present rather than assumed.

Diagonal wires are not supported — LTspice draws orthogonal wires in normal
use, and treating a diagonal as a connector would invent nodes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from mcp_ltspice.asc_io import from_ltspice_value, read_asc_text
from rf_mcp_common.logging import get_logger

log = get_logger("mcp_ltspice.asc_netlist")

#: Pin offsets from the stock LTspice symbol library (``lib/sym/*.asy``),
#: in symbol-local coordinates and SPICE pin order.
PIN_OFFSETS: dict[str, tuple[tuple[int, int], ...]] = {
    "voltage": ((0, 16), (0, 96)),
    "current": ((0, 16), (0, 96)),
    "res": ((16, 16), (16, 96)),
    "ind": ((16, 16), (16, 96)),
    "cap": ((16, 0), (16, 64)),
    "ind2": ((16, 16), (16, 96)),
    "polcap": ((16, 0), (16, 64)),
}

#: SPICE element letter for each symbol kind.
SPICE_PREFIX = {
    "res": "R",
    "ind": "L",
    "ind2": "L",
    "cap": "C",
    "polcap": "C",
    "voltage": "V",
    "current": "I",
}

Point = tuple[int, int]


def _rotate(rot: str, dx: int, dy: int) -> Point:
    """Apply an LTspice symbol rotation to a pin offset.

    The R90 mapping was verified against LTspice 26.0.2 by netlisting a probe
    schematic and checking which net labels attached; the mirrored variants
    follow from flipping x before rotating.
    """
    mirrored = rot.startswith("M")
    angle = int(rot[1:]) if len(rot) > 1 else 0
    if mirrored:
        dx = -dx
    if angle == 0:
        return dx, dy
    if angle == 90:
        return -dy, dx
    if angle == 180:
        return -dx, -dy
    if angle == 270:
        return dy, -dx
    raise ValueError(f"Unsupported symbol rotation {rot!r}")


@dataclass
class Symbol:
    kind: str
    x: int
    y: int
    rot: str
    inst: str | None = None
    value: str | None = None
    attrs: dict[str, str] = field(default_factory=dict)

    def pins(self) -> list[Point]:
        if self.kind not in PIN_OFFSETS:
            raise NotImplementedError(
                f"Symbol kind {self.kind!r} (instance {self.inst}) has no known pin "
                "geometry, so it cannot be netlisted. Supported: "
                f"{', '.join(sorted(PIN_OFFSETS))}."
            )
        out = []
        for dx, dy in PIN_OFFSETS[self.kind]:
            rdx, rdy = _rotate(self.rot, dx, dy)
            out.append((self.x + rdx, self.y + rdy))
        return out


@dataclass
class AscSchematic:
    symbols: list[Symbol]
    wires: list[tuple[int, int, int, int]]
    flags: list[tuple[int, int, str]]
    directives: list[str]


def parse_asc(asc_path: str | Path) -> AscSchematic:
    """Parse the subset of ``.asc`` needed to netlist: symbols, wires, flags."""
    doc = read_asc_text(asc_path)
    symbols: list[Symbol] = []
    wires: list[tuple[int, int, int, int]] = []
    flags: list[tuple[int, int, str]] = []
    directives: list[str] = []

    for line in doc.text.splitlines():
        line = line.strip()
        if line.startswith("SYMBOL "):
            parts = line.split()
            if len(parts) < 5:
                continue
            symbols.append(Symbol(kind=parts[1], x=int(parts[2]), y=int(parts[3]), rot=parts[4]))
        elif line.startswith("SYMATTR ") and symbols:
            parts = line.split(maxsplit=2)
            if len(parts) < 3:
                continue
            key, val = parts[1], parts[2].strip()
            if key == "InstName":
                symbols[-1].inst = val
            elif key == "Value":
                symbols[-1].value = val
            symbols[-1].attrs[key] = val
        elif line.startswith("WIRE "):
            parts = line.split()
            wires.append((int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4])))
        elif line.startswith("FLAG "):
            parts = line.split()
            flags.append((int(parts[1]), int(parts[2]), parts[3]))
        elif line.startswith("TEXT ") and "!" in line:
            directives.append(line.split("!", 1)[1].strip())

    return AscSchematic(symbols=symbols, wires=wires, flags=flags, directives=directives)


class _Union:
    def __init__(self) -> None:
        self.parent: dict[Point, Point] = {}

    def find(self, a: Point) -> Point:
        self.parent.setdefault(a, a)
        root = a
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[a] != root:  # path compression
            self.parent[a], a = root, self.parent[a]
        return root

    def union(self, a: Point, b: Point) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _on_segment(p: Point, seg: tuple[int, int, int, int]) -> bool:
    """True if ``p`` lies on the axis-aligned wire ``seg`` (endpoints included)."""
    x1, y1, x2, y2 = seg
    px, py = p
    if x1 == x2:
        return px == x1 and min(y1, y2) <= py <= max(y1, y2)
    if y1 == y2:
        return py == y1 and min(x1, x2) <= px <= max(x1, x2)
    return False  # diagonal: not treated as a connector


def build_nodes(sch: AscSchematic) -> dict[Point, str]:
    """Assign a SPICE node name to every electrically interesting point.

    Ground (an LTspice flag named ``0``) is normalised to node ``0``, which
    is what SPICE requires.
    """
    uf = _Union()
    points: set[Point] = set()

    for sym in sch.symbols:
        points.update(sym.pins())
    for x, y, _ in sch.flags:
        points.add((x, y))
    for x1, y1, x2, y2 in sch.wires:
        points.add((x1, y1))
        points.add((x2, y2))

    for seg in sch.wires:
        uf.union((seg[0], seg[1]), (seg[2], seg[3]))
        # A pin or flag touching the middle of a wire joins that wire's node —
        # LTspice draws a junction dot for exactly this case.
        for p in points:
            if _on_segment(p, seg):
                uf.union((seg[0], seg[1]), p)

    # Coincident points are the same node even with no wire between them.
    for p in points:
        uf.find(p)

    # Name each root: prefer an explicit flag name, else an auto node number.
    names: dict[Point, str] = {}
    root_name: dict[Point, str] = {}
    for x, y, label in sch.flags:
        root = uf.find((x, y))
        name = "0" if label in ("0", "GND", "gnd") else label
        # A ground flag anywhere on a net wins over a user label.
        if root_name.get(root) != "0":
            root_name[root] = name

    counter = 1
    for p in sorted(points):
        root = uf.find(p)
        if root not in root_name:
            root_name[root] = f"N{counter:03d}"
            counter += 1
        names[p] = root_name[root]
    return names


def netlist_from_asc(
    asc_path: str | Path,
    *,
    port1: str = "p1",
    port2: str = "p2",
) -> tuple[str, float]:
    """Build an ngspice netlist from a schematic's geometry.

    Returns ``(netlist_text, z0)`` where ``z0`` is read from the source
    resistor ``Rs1`` if present, so a 75 Ω design simulates at 75 Ω instead
    of the hardcoded 50 Ω the old netlister always used.
    """
    sch = parse_asc(asc_path)
    if not sch.symbols:
        raise ValueError(f"{asc_path} contains no symbols to netlist.")
    nodes = build_nodes(sch)

    lines = [f"* ngspice netlist from {Path(asc_path).name} (geometry-derived)"]
    z0 = 50.0
    seen_ports = set()

    for sym in sch.symbols:
        if sym.inst is None:
            raise ValueError(f"Symbol {sym.kind} at ({sym.x},{sym.y}) has no InstName.")
        prefix = SPICE_PREFIX.get(sym.kind)
        if prefix is None:
            raise NotImplementedError(
                f"Symbol kind {sym.kind!r} (instance {sym.inst}) cannot be netlisted."
            )
        pins = sym.pins()
        try:
            pin_nodes = [nodes[p] for p in pins]
        except KeyError as e:  # pragma: no cover - build_nodes covers every pin
            raise ValueError(f"{sym.inst}: pin {e} was not assigned a node") from None

        if sym.inst.upper() == "RS1" and sym.value:
            with_suppress = _safe_value(sym.value)
            if with_suppress is not None:
                z0 = with_suppress

        for pn in pin_nodes:
            if pn in (port1, port2):
                seen_ports.add(pn)

        value = sym.value or "0"
        if sym.kind == "voltage":
            # Keep an AC spec as-is; bare numbers become a DC source.
            spec = value if value.upper().startswith("AC") else f"AC {value}"
            lines.append(f"{sym.inst} {pin_nodes[0]} {pin_nodes[1]} {spec}")
        else:
            numeric = _safe_value(value)
            rendered = f"{numeric:.10g}" if numeric is not None else value
            lines.append(f"{sym.inst} {pin_nodes[0]} {pin_nodes[1]} {rendered}")

    missing = {port1, port2} - seen_ports
    if missing:
        raise ValueError(
            f"{asc_path} has no net labelled {sorted(missing)}. S-parameter "
            f"extraction needs flags named {port1!r} and {port2!r} at the ports."
        )

    ac = _ac_directive(sch.directives)
    lines.append(ac)
    lines.append(f".save V({port1}) V({port2})")
    lines.append(".end")
    return "\n".join(lines) + "\n", z0


def _safe_value(raw: str) -> float | None:
    try:
        return from_ltspice_value(raw)
    except (ValueError, TypeError):
        return None


def _ac_directive(directives: list[str]) -> str:
    """Recover the ``.ac`` directive from the schematic, or use a default."""
    for d in directives:
        if d.lower().startswith(".ac"):
            return d
    log.warning("No .ac directive in schematic; defaulting to `.ac dec 200 1e6 5e9`.")
    return ".ac dec 200 1e6 5e9"


_REFDES_RE = re.compile(r"^([A-Za-z]+)(\d+)$")
