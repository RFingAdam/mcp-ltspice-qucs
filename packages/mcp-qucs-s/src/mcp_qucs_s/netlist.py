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

from mcp_qucs_s.microstrip import C0, Substrate

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
    "series_tline",  # ideal transmission line in the main path — distributed filters
    "coupled_line_section",  # ideal coupled-line BPF section (CTLIN, diagonal, others open)
]

SweepType = Literal["log", "lin"]

#: Element kinds that advance the signal node (everything else shunts).
_SERIES_KINDS = frozenset(
    {
        "series_l",
        "series_c",
        "series_lc_series",
        "series_lc_parallel",
        "series_tline",
        "coupled_line_section",
    }
)

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

    if kind == "series_tline":
        # qucsator's TLIN is an ideal line propagating at c (probe-verified:
        # a 74.948114 mm line is exactly 90° at 1 GHz), so the emitted
        # physical length encodes the electrical length at the reference
        # frequency: L = θ/360 · c/f_ref. Length in metres, plain SI float.
        out = nodes.next()
        length_m = (need("theta_deg") / 360.0) * C0 / need("f_ref_hz")
        lines.append(
            f'TLIN:TL{index} {node_in} {out} Z="{_fmt(need("z0_ohm"))} Ohm" '
            f'L="{_fmt(length_m)}" Alpha="0 dB"'
        )
        return lines, out

    if kind == "coupled_line_section":
        # Ideal coupled-line BPF section. qucsator's CTLIN node order is
        # line1-near, line1-far, line2-far, line2-near (probe-verified);
        # the bandpass connection is the diagonal — in at line1-near, out
        # at line2-far — with the other two ends left open. CTLIN, like
        # TLIN, propagates at c, so L = θ/360 · c/f_ref.
        open_a = nodes.next("_o")
        out = nodes.next()
        open_b = nodes.next("_o")
        length_m = (need("theta_deg") / 360.0) * C0 / need("f_ref_hz")
        lines.append(
            f"CTLIN:CL{index} {node_in} {open_a} {out} {open_b} "
            f'Ze="{_fmt(need("z0e_ohm"))} Ohm" Zo="{_fmt(need("z0o_ohm"))} Ohm" '
            f'L="{_fmt(length_m)}"'
        )
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


def _validate_sweep(f_start_hz: float, f_stop_hz: float, points: int, z0: float) -> None:
    if f_stop_hz <= f_start_hz:
        raise ValueError(f"f_stop_hz ({f_stop_hz}) must exceed f_start_hz ({f_start_hz})")
    if points < 2:
        raise ValueError(f"points must be at least 2; got {points}")
    if z0 <= 0:
        raise ValueError(f"z0 must be positive; got {z0}")


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
    _validate_sweep(f_start_hz, f_stop_hz, points, z0)

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


#: Copper bulk resistivity (Ω·m) and a typical surface roughness for the
#: SUBST card. Losses barely move S-parameter *shape* comparisons; these
#: are sane fixed defaults rather than user-facing knobs.
_COPPER_RHO = 1.72e-8
_SURFACE_ROUGHNESS_M = 0.15e-6


def generate_microstrip_ladder_netlist(
    sections: list[dict[str, float]],
    substrate: Substrate,
    output_path: str | Path,
    *,
    z0: float = 50.0,
    f_start_hz: float = 1e6,
    f_stop_hz: float = 5e9,
    points: int = 200,
    sweep: SweepType = "log",
    title: str = "Generated by mcp-qucs-s",
) -> Path:
    """Write a qucsator netlist for a 2-port cascade of microstrip lines.

    ``sections`` is an ordered source-to-load list of
    ``{"width_mm": ..., "length_mm": ...}`` dicts — e.g. the ``sections``
    of :func:`mcp_qucs_s.distributed.stepped_impedance_lpf`. Each becomes
    an ``MLIN`` (Hammerstad model, Kirschning dispersion) referencing one
    shared ``SUBST`` card, so the simulation exercises qucsator's *real*
    microstrip model — dispersion and losses included — rather than the
    ideal-line approximation.

    Step discontinuities between sections are not modelled (no ``MSTEP``
    elements); for stepped-impedance filters the step effect is small
    next to the βl < 45° approximation already inherent in the synthesis.
    """
    if not sections:
        raise ValueError("Cannot generate a netlist with no sections.")
    _validate_sweep(f_start_hz, f_stop_hz, points, z0)
    for i, s in enumerate(sections, start=1):
        for key in ("width_mm", "length_mm"):
            if key not in s:
                raise ValueError(f"section {i} is missing required parameter {key!r}")
            if s[key] <= 0:
                raise ValueError(f"section {i}: {key} must be positive; got {s[key]}")

    out_path = Path(output_path).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    nodes = _NodeAllocator()
    body: list[str] = [
        f'SUBST:Subst1 er="{_fmt(substrate.er)}" h="{_fmt(substrate.h_mm)} mm" '
        f't="{_fmt(substrate.t_um)} um" tand="{_fmt(substrate.tan_d)}" '
        f'rho="{_fmt(_COPPER_RHO)}" D="{_fmt(_SURFACE_ROUGHNESS_M)}"'
    ]
    node = PORT1_NODE
    for i, s in enumerate(sections, start=1):
        out = nodes.next()
        body.append(
            f'MLIN:MS{i} {node} {out} Subst="Subst1" W="{_fmt(s["width_mm"])} mm" '
            f'L="{_fmt(s["length_mm"])} mm" Model="Hammerstad" DispModel="Kirschning"'
        )
        node = out

    port_z = f'Z="{_fmt(z0)} Ohm"'
    header = [
        f"# {title}",
        f"# 2-port microstrip cascade, {len(sections)} sections, Z0 = {_fmt(z0)} Ohm",
        f'Pac:P1 {PORT1_NODE} {GROUND} Num="1" {port_z} P="0 dBm" f="1 GHz"',
        f'Pac:P2 {node} {GROUND} Num="2" {port_z} P="0 dBm" f="1 GHz"',
    ]
    analysis = [
        f'.SP:SP1 Type="{sweep}" Start="{_fmt(f_start_hz)}" '
        f'Stop="{_fmt(f_stop_hz)}" Points="{points}"'
    ]

    out_path.write_text("\n".join(header + body + analysis) + "\n", encoding="utf-8")
    return out_path


def generate_coupled_microstrip_netlist(
    sections: list[dict[str, float]],
    substrate: Substrate,
    output_path: str | Path,
    *,
    z0: float = 50.0,
    f_start_hz: float = 1e6,
    f_stop_hz: float = 5e9,
    points: int = 200,
    sweep: SweepType = "log",
    title: str = "Generated by mcp-qucs-s",
) -> Path:
    """Write a qucsator netlist for a diagonally-chained cascade of
    coupled microstrip sections (an edge-coupled BPF).

    ``sections`` is an ordered source-to-load list of
    ``{"width_mm": ..., "gap_mm": ..., "length_mm": ...}`` dicts — e.g.
    the ``sections`` of :func:`mcp_qucs_s.distributed.coupled_line_bpf`.
    Each becomes an ``MCOUPLED`` (Kirschning model and dispersion)
    referencing one shared ``SUBST`` card. Sections chain diagonally —
    the line2-far node of one section is the line1-near node of the
    next — with each section's line1-far and line2-near ends open, the
    edge-coupled filter connection (probe-verified node order).
    """
    if not sections:
        raise ValueError("Cannot generate a netlist with no sections.")
    _validate_sweep(f_start_hz, f_stop_hz, points, z0)
    for i, s in enumerate(sections, start=1):
        for key in ("width_mm", "gap_mm", "length_mm"):
            if key not in s:
                raise ValueError(f"section {i} is missing required parameter {key!r}")
            if s[key] <= 0:
                raise ValueError(f"section {i}: {key} must be positive; got {s[key]}")

    out_path = Path(output_path).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    nodes = _NodeAllocator()
    body: list[str] = [
        f'SUBST:Subst1 er="{_fmt(substrate.er)}" h="{_fmt(substrate.h_mm)} mm" '
        f't="{_fmt(substrate.t_um)} um" tand="{_fmt(substrate.tan_d)}" '
        f'rho="{_fmt(_COPPER_RHO)}" D="{_fmt(_SURFACE_ROUGHNESS_M)}"'
    ]
    node = PORT1_NODE
    for i, s in enumerate(sections, start=1):
        open_a = nodes.next("_o")
        out = nodes.next()
        open_b = nodes.next("_o")
        body.append(
            f'MCOUPLED:MS{i} {node} {open_a} {out} {open_b} Subst="Subst1" '
            f'W="{_fmt(s["width_mm"])} mm" L="{_fmt(s["length_mm"])} mm" '
            f'S="{_fmt(s["gap_mm"])} mm" Model="Kirschning" DispModel="Kirschning"'
        )
        node = out

    port_z = f'Z="{_fmt(z0)} Ohm"'
    header = [
        f"# {title}",
        f"# 2-port edge-coupled cascade, {len(sections)} sections, Z0 = {_fmt(z0)} Ohm",
        f'Pac:P1 {PORT1_NODE} {GROUND} Num="1" {port_z} P="0 dBm" f="1 GHz"',
        f'Pac:P2 {node} {GROUND} Num="2" {port_z} P="0 dBm" f="1 GHz"',
    ]
    analysis = [
        f'.SP:SP1 Type="{sweep}" Start="{_fmt(f_start_hz)}" '
        f'Stop="{_fmt(f_stop_hz)}" Points="{points}"'
    ]

    out_path.write_text("\n".join(header + body + analysis) + "\n", encoding="utf-8")
    return out_path


def generate_hairpin_netlist(
    design: dict,
    substrate: Substrate,
    output_path: str | Path,
    *,
    z0: float = 50.0,
    f_start_hz: float = 1e6,
    f_stop_hz: float = 5e9,
    points: int = 200,
    sweep: SweepType = "log",
    title: str = "Generated by mcp-qucs-s",
) -> Path:
    """Write a qucsator netlist for a hairpin BPF — the diagonally-chained
    MCOUPLED cascade of :func:`generate_coupled_microstrip_netlist` with
    each resonator's U-bend inserted as an ``MLIN`` at the internal
    junctions (exactly where the diagonal chain joins the two arm halves
    of one resonator; the outer feed ends carry the ports, so a design
    with N+1 sections gets N bends).

    ``design`` is the dict returned by
    :func:`mcp_qucs_s.distributed.hairpin_bpf` — ``sections`` supplies
    per-section (W, S, L), ``bend_mm``/``bend_width_mm`` the connector.
    A zero-length bend emits the plain coupled cascade.
    """
    sections = design.get("sections")
    if not sections:
        raise ValueError("design has no sections to netlist.")
    _validate_sweep(f_start_hz, f_stop_hz, points, z0)
    bend_mm = float(design.get("bend_mm", 0.0))
    bend_w_mm = float(design.get("bend_width_mm", 0.0))
    if bend_mm > 0 and bend_w_mm <= 0:
        raise ValueError("design carries a bend but no bend_width_mm.")

    out_path = Path(output_path).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    nodes = _NodeAllocator()
    body: list[str] = [
        f'SUBST:Subst1 er="{_fmt(substrate.er)}" h="{_fmt(substrate.h_mm)} mm" '
        f't="{_fmt(substrate.t_um)} um" tand="{_fmt(substrate.tan_d)}" '
        f'rho="{_fmt(_COPPER_RHO)}" D="{_fmt(_SURFACE_ROUGHNESS_M)}"'
    ]
    node = PORT1_NODE
    last = len(sections)
    for i, s in enumerate(sections, start=1):
        open_a = nodes.next("_o")
        out = nodes.next()
        open_b = nodes.next("_o")
        body.append(
            f'MCOUPLED:MS{i} {node} {open_a} {out} {open_b} Subst="Subst1" '
            f'W="{_fmt(s["width_mm"])} mm" L="{_fmt(s["length_mm"])} mm" '
            f'S="{_fmt(s["gap_mm"])} mm" Model="Kirschning" DispModel="Kirschning"'
        )
        node = out
        if i < last and bend_mm > 0:
            bend_out = nodes.next()
            body.append(
                f'MLIN:BEND{i} {node} {bend_out} Subst="Subst1" '
                f'W="{_fmt(bend_w_mm)} mm" L="{_fmt(bend_mm)} mm" '
                f'Model="Hammerstad" DispModel="Kirschning"'
            )
            node = bend_out

    port_z = f'Z="{_fmt(z0)} Ohm"'
    header = [
        f"# {title}",
        f"# 2-port hairpin cascade, {len(sections)} sections, Z0 = {_fmt(z0)} Ohm",
        f'Pac:P1 {PORT1_NODE} {GROUND} Num="1" {port_z} P="0 dBm" f="1 GHz"',
        f'Pac:P2 {node} {GROUND} Num="2" {port_z} P="0 dBm" f="1 GHz"',
    ]
    analysis = [
        f'.SP:SP1 Type="{sweep}" Start="{_fmt(f_start_hz)}" '
        f'Stop="{_fmt(f_stop_hz)}" Points="{points}"'
    ]

    out_path.write_text("\n".join(header + body + analysis) + "\n", encoding="utf-8")
    return out_path


def generate_interdigital_netlist(
    design: dict,
    output_path: str | Path,
    *,
    z0: float = 50.0,
    f_start_hz: float = 1e6,
    f_stop_hz: float = 5e9,
    points: int = 200,
    sweep: SweepType = "log",
    title: str = "Generated by mcp-qucs-s",
) -> Path:
    """Write the exact ideal-TEM graph netlist of an interdigital array.

    Uses the linearity of the array's 2N-port in Y_c: per commensurate
    segment, each line becomes an ordinary ``TLIN`` stub of admittance
    ``Y_ii − Σ|mutuals|`` between its own boundary nodes, and each
    adjacent-pair coupling becomes a floating ``TLIN4P`` of admittance
    ``y_m`` (probe-verified: this reproduces CTLIN at 0.0000 mdB, node
    order line1-near / line1-far / line2-far / line2-near). Shorts land
    on ``gnd``, opens dangle, ports sit on the tap nodes. All lines are
    vacuum-velocity ideal, ``L = θ/360 · c/f0``.

    ``design`` is the dict from
    :func:`mcp_qucs_s.distributed.interdigital_bpf` (``y_c``,
    ``segments_deg``, ``bottom``, ``top``, ``ports``, ``f0_hz``).
    """
    import numpy as np

    y_c = np.asarray(design["y_c"], dtype=float)
    segments = [float(t) for t in design["segments_deg"]]
    bottom = list(design["bottom"])
    top = list(design["top"])
    ports = [tuple(p) for p in design["ports"]]
    f0 = float(design["f0_hz"])
    n_lines = y_c.shape[0]
    n_seg = len(segments)
    _validate_sweep(f_start_hz, f_stop_hz, points, z0)

    out_path = Path(output_path).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def node(level: int, line: int) -> str:
        if level == 0 and bottom[line] == "short":
            return GROUND
        if level == n_seg and top[line] == "short":
            return GROUND
        return f"_v{level}l{line + 1}"

    body: list[str] = []
    for s, theta in enumerate(segments):
        length_m = (theta / 360.0) * C0 / f0
        for i in range(n_lines):
            mutual = sum(abs(y_c[i, j]) for j in range(n_lines) if j != i)
            y_stub = y_c[i, i] - mutual
            body.append(
                f"TLIN:S{s + 1}L{i + 1} {node(s, i)} {node(s + 1, i)} "
                f'Z="{_fmt(1.0 / y_stub)} Ohm" L="{_fmt(length_m)}" Alpha="0 dB"'
            )
        for i in range(n_lines - 1):
            y_m = abs(y_c[i, i + 1])
            if y_m == 0.0:
                continue
            body.append(
                f"TLIN4P:F{s + 1}L{i + 1} {node(s, i)} {node(s + 1, i)} "
                f"{node(s + 1, i + 1)} {node(s, i + 1)} "
                f'Z="{_fmt(1.0 / y_m)} Ohm" L="{_fmt(length_m)}" Alpha="0 dB"'
            )

    port_z = f'Z="{_fmt(z0)} Ohm"'
    header = [
        f"# {title}",
        f"# interdigital graph netlist: {n_lines} lines, {n_seg} segments",
    ]
    for num, (level, line) in enumerate(ports, start=1):
        header.append(
            f'Pac:P{num} {node(level, line)} {GROUND} Num="{num}" {port_z} P="0 dBm" f="1 GHz"'
        )
    analysis = [
        f'.SP:SP1 Type="{sweep}" Start="{_fmt(f_start_hz)}" '
        f'Stop="{_fmt(f_stop_hz)}" Points="{points}"'
    ]

    out_path.write_text("\n".join(header + body + analysis) + "\n", encoding="utf-8")
    return out_path
