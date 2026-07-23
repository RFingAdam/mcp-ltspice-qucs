"""Qucs netlist generation for lumped LC ladders.

``qucsator_rf`` simulates a *netlist*, not the GUI's ``.sch`` file — the
GUI netlists a schematic before handing it over. Until this module existed
nothing in the package could produce that input, so ``run_sp_analysis``,
``export_touchstone`` and ``extract_noise_parameters`` were unreachable:
every one of them needed a file the user had to author by hand in the GUI
first.

Elements are specified by **explicit position**, e.g. ``series_l`` versus
``shunt_l``, rather than inferred from a refdes letter. Inferring position
from the letter is what makes the ngspice netlister in ``mcp-ltspice``
silently emit the dual network for any non-lowpass ladder (issue #32), and
that mistake is not worth repeating here.

Format notes, verified against qucsator-RF 1.0.7:

- Ports are ``Pac`` sources carrying ``Num``/``Z``; port impedance is what
  the S-parameter analysis normalises to.
- Ground is the reserved node name ``gnd``.
- ``.SP`` drives the sweep; ``Type="log"`` matches the log-spaced grids the
  rest of this suite uses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

LadderElementType = Literal[
    "series_l",
    "series_c",
    "shunt_l",
    "shunt_c",
    "shunt_lc_trap",  # series LC to ground — notch / elliptic trap
    "shunt_lc_parallel",  # parallel LC to ground — BPF shunt section
    "series_lc_series",  # series LC in the main path — BPF series section
    "series_lc_parallel",  # parallel LC in the main path — BSF series section
    "shunt_composite_trap",  # series LC + parallel LC in series, to ground — elliptic BPF/BSF trap
]

SweepType = Literal["log", "lin"]

#: Element kinds that advance the signal node (everything else shunts).
_SERIES_KINDS = frozenset({"series_l", "series_c", "series_lc_series", "series_lc_parallel"})

PORT1_NODE = "_p1"
GROUND = "gnd"


def _fmt(value: float) -> str:
    """Qucs accepts plain SI-unit floats; 10 significant digits is plenty."""
    return f"{value:.10g}"


class _NodeAllocator:
    def __init__(self) -> None:
        self._n = 0

    def next(self, prefix: str = "_n") -> str:
        self._n += 1
        return f"{prefix}{self._n}"


def _emit_element(
    kind: str,
    params: dict[str, float],
    index: int,
    node_in: str,
    nodes: _NodeAllocator,
) -> tuple[list[str], str]:
    """Emit one element. Returns its lines and the resulting signal node."""
    lines: list[str] = []

    def need(key: str) -> float:
        if key not in params:
            raise ValueError(f"element {index} ({kind}) is missing required parameter {key!r}")
        return float(params[key])

    if kind == "series_l":
        out = nodes.next()
        lines.append(f'L:L{index} {node_in} {out} L="{_fmt(need("L"))}"')
        return lines, out

    if kind == "series_c":
        out = nodes.next()
        lines.append(f'C:C{index} {node_in} {out} C="{_fmt(need("C"))}"')
        return lines, out

    if kind == "shunt_l":
        lines.append(f'L:L{index} {node_in} {GROUND} L="{_fmt(need("L"))}"')
        return lines, node_in

    if kind == "shunt_c":
        lines.append(f'C:C{index} {node_in} {GROUND} C="{_fmt(need("C"))}"')
        return lines, node_in

    if kind == "shunt_lc_trap":
        mid = nodes.next("_t")
        lines.append(f'L:L{index} {node_in} {mid} L="{_fmt(need("L"))}"')
        lines.append(f'C:C{index} {mid} {GROUND} C="{_fmt(need("C"))}"')
        return lines, node_in

    if kind == "shunt_lc_parallel":
        lines.append(f'L:L{index} {node_in} {GROUND} L="{_fmt(need("L"))}"')
        lines.append(f'C:C{index} {node_in} {GROUND} C="{_fmt(need("C"))}"')
        return lines, node_in

    if kind == "series_lc_series":
        mid = nodes.next("_t")
        out = nodes.next()
        lines.append(f'L:L{index} {node_in} {mid} L="{_fmt(need("L"))}"')
        lines.append(f'C:C{index} {mid} {out} C="{_fmt(need("C"))}"')
        return lines, out

    if kind == "series_lc_parallel":
        out = nodes.next()
        lines.append(f'L:L{index} {node_in} {out} L="{_fmt(need("L"))}"')
        lines.append(f'C:C{index} {node_in} {out} C="{_fmt(need("C"))}"')
        return lines, out

    if kind == "shunt_composite_trap":
        # Elliptic BPF/BSF trap image: series-LC (L_s, C_s) from the signal
        # node into the tank node, then the parallel tank (L_p ∥ C_p) to
        # ground. The branch shorts at its two mapped transmission zeros.
        mid = nodes.next("_t")
        tank = nodes.next("_t")
        lines.append(f'L:L{index}s {node_in} {mid} L="{_fmt(need("L_s"))}"')
        lines.append(f'C:C{index}s {mid} {tank} C="{_fmt(need("C_s"))}"')
        lines.append(f'L:L{index}p {tank} {GROUND} L="{_fmt(need("L_p"))}"')
        lines.append(f'C:C{index}p {tank} {GROUND} C="{_fmt(need("C_p"))}"')
        return lines, node_in

    raise ValueError(
        f"Unknown ladder element {kind!r} at position {index}. "
        f"Expected one of: {', '.join(sorted(_SERIES_KINDS | {'shunt_l', 'shunt_c', 'shunt_lc_trap', 'shunt_lc_parallel', 'shunt_composite_trap'}))}"
    )


def generate_ladder_netlist(
    elements: list[tuple[str, dict[str, float]]],
    output_path: str | Path,
    *,
    z0: float = 50.0,
    f_start_hz: float = 1e6,
    f_stop_hz: float = 5e9,
    points: int = 200,
    sweep: SweepType = "log",
    title: str = "Generated by mcp-qucs-s",
) -> Path:
    """Write a qucsator netlist for a 2-port lumped ladder.

    ``elements`` is an ordered source-to-load list of
    ``(kind, params)`` pairs, e.g.::

        [("series_l", {"L": 7.96e-9}),
         ("shunt_c",  {"C": 6.37e-12}),
         ("series_l", {"L": 7.96e-9})]

    Port 2 is attached to whatever node the walk ends on, so a ladder made
    only of shunt elements correctly puts both ports on the same node
    rather than needing a fictitious zero-ohm link.
    """
    if not elements:
        raise ValueError("Cannot generate a netlist with no elements.")
    if f_stop_hz <= f_start_hz:
        raise ValueError(f"f_stop_hz ({f_stop_hz}) must exceed f_start_hz ({f_start_hz})")
    if points < 2:
        raise ValueError(f"points must be at least 2; got {points}")
    if z0 <= 0:
        raise ValueError(f"z0 must be positive; got {z0}")

    out_path = Path(output_path).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    nodes = _NodeAllocator()
    body: list[str] = []
    node = PORT1_NODE

    for position, (kind, params) in enumerate(elements, start=1):
        lines, node = _emit_element(kind, params, position, node, nodes)
        body.extend(lines)

    port_z = f'Z="{_fmt(z0)} Ohm"'
    header = [
        f"# {title}",
        f"# 2-port ladder, {len(elements)} elements, Z0 = {_fmt(z0)} Ohm",
        f'Pac:P1 {PORT1_NODE} {GROUND} Num="1" {port_z} P="0 dBm" f="1 GHz"',
        f'Pac:P2 {node} {GROUND} Num="2" {port_z} P="0 dBm" f="1 GHz"',
    ]
    analysis = [
        f'.SP:SP1 Type="{sweep}" Start="{_fmt(f_start_hz)}" '
        f'Stop="{_fmt(f_stop_hz)}" Points="{points}"'
    ]

    out_path.write_text("\n".join(header + body + analysis) + "\n", encoding="utf-8")
    return out_path
