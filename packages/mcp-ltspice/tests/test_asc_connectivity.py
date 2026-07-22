"""Electrical-connectivity tests for generated ``.asc`` schematics.

Background: ``generate_lpf_asc`` used to emit symbols and net labels but no
``WIRE`` statements at all (the ``_wire`` helper was dead code). LTspice
netlists such a schematic with every component on its own ``NC_*`` nodes, so
the AC analysis yielded ``No. Points: 0`` and S-parameter extraction failed
with the thoroughly unhelpful "No plots found in the RAW file".

Nothing caught it: the only end-to-end tests asserted ``raw_path.is_file()``
and never extracted, and the extraction unit tests monkeypatch ``spicelib``
so they never read a real ``.raw``.

:func:`assert_fully_connected` closes that gap *without needing a simulator*,
so CI catches a regression here even though it has neither LTspice nor
ngspice. It reimplements LTspice's connectivity rule — a pin connects to
whatever shares its coordinate — using pin offsets read from the stock
symbol library and the rotation convention verified against LTspice itself.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mcp_ltspice.asc_io import generate_lpf_asc
from mcp_ltspice.synthesis.lc_filter import synthesize_lc_lpf

# Pin offsets from the stock LTspice symbol library (lib/sym/*.asy).
PIN_OFFSETS: dict[str, list[tuple[int, int]]] = {
    "voltage": [(0, 16), (0, 96)],
    "res": [(16, 16), (16, 96)],
    "ind": [(16, 16), (16, 96)],
    "cap": [(16, 0), (16, 64)],
}

# Rotation convention verified empirically against LTspice 26.0.2: a symbol
# at R90 with pin offset (dx, dy) puts that pin at (X - dy, Y + dx).
ROTATIONS = {
    "R0": lambda dx, dy: (dx, dy),
    "R90": lambda dx, dy: (-dy, dx),
    "R180": lambda dx, dy: (-dx, -dy),
    "R270": lambda dx, dy: (dy, -dx),
}


def parse_asc(asc_path: Path) -> dict[str, list]:
    """Pull symbols, wires and flags out of an ``.asc``."""
    symbols: list[tuple[str, str, int, int, str]] = []
    wires: list[tuple[int, int, int, int]] = []
    flags: list[tuple[int, int, str]] = []
    pending: tuple[str, int, int, str] | None = None
    inst: str | None = None

    for line in asc_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("SYMBOL "):
            if pending is not None and inst is not None:
                symbols.append((inst, *pending))
            _, kind, x, y, rot = line.split()
            pending, inst = (kind, int(x), int(y), rot), None
        elif line.startswith("SYMATTR InstName "):
            inst = line.split(maxsplit=2)[2].strip()
        elif line.startswith("WIRE "):
            _, x1, y1, x2, y2 = line.split()
            wires.append((int(x1), int(y1), int(x2), int(y2)))
        elif line.startswith("FLAG "):
            _, x, y, name = line.split()
            flags.append((int(x), int(y), name))

    if pending is not None and inst is not None:
        symbols.append((inst, *pending))
    return {"symbols": symbols, "wires": wires, "flags": flags}


def pin_positions(
    symbols: list[tuple[str, str, int, int, str]],
) -> dict[str, list[tuple[int, int]]]:
    """Absolute coordinates of every pin, keyed by refdes."""
    out: dict[str, list[tuple[int, int]]] = {}
    for inst, kind, x, y, rot in symbols:
        if kind not in PIN_OFFSETS:
            raise AssertionError(f"{inst}: unknown symbol kind {kind!r}")
        if rot not in ROTATIONS:
            raise AssertionError(f"{inst}: unhandled rotation {rot!r}")
        rotate = ROTATIONS[rot]
        out[inst] = [
            (x + dxr, y + dyr) for dxr, dyr in (rotate(dx, dy) for dx, dy in PIN_OFFSETS[kind])
        ]
    return out


def assert_fully_connected(asc_path: Path) -> None:
    """Fail if any component pin is electrically floating.

    A pin counts as connected when its coordinate coincides with another
    pin, a wire endpoint, or a net label — exactly the cases LTspice treats
    as a node. A pin matching none of those becomes an ``NC_*`` net.
    """
    parsed = parse_asc(asc_path)
    pins = pin_positions(parsed["symbols"])

    wire_endpoints = set()
    for x1, y1, x2, y2 in parsed["wires"]:
        wire_endpoints.add((x1, y1))
        wire_endpoints.add((x2, y2))
    flag_points = {(x, y) for x, y, _ in parsed["flags"]}

    seen: dict[tuple[int, int], list[str]] = {}
    for inst, coords in pins.items():
        for c in coords:
            seen.setdefault(c, []).append(inst)

    floating: list[str] = []
    for inst, coords in pins.items():
        for c in coords:
            shares_with_pin = len(seen[c]) > 1
            if not (shares_with_pin or c in wire_endpoints or c in flag_points):
                floating.append(f"{inst} pin at {c}")

    assert not floating, (
        "Schematic has floating pins — LTspice will netlist these as NC_* "
        "nodes and the AC analysis will yield zero points:\n  " + "\n  ".join(floating)
    )


# ---------------------------------------------------------------------------
# Structural tests — no simulator required, so these run everywhere.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("order", [3, 5, 7])
def test_generated_lpf_asc_has_no_floating_pins(tmp_path, order: int) -> None:
    design = synthesize_lc_lpf("butterworth", order=order, cutoff_hz=1e9)
    asc = generate_lpf_asc(design.components, tmp_path / f"lpf{order}.asc")
    assert_fully_connected(asc)


def test_generated_lpf_asc_emits_wires(tmp_path) -> None:
    """The regression that started it all: not a single WIRE was emitted."""
    design = synthesize_lc_lpf("butterworth", order=3, cutoff_hz=1e9)
    asc = generate_lpf_asc(design.components, tmp_path / "lpf.asc")
    assert parse_asc(asc)["wires"], "generated .asc contains no WIRE statements"


def test_generated_lpf_asc_labels_ports_and_ground(tmp_path) -> None:
    design = synthesize_lc_lpf("butterworth", order=3, cutoff_hz=1e9)
    asc = generate_lpf_asc(design.components, tmp_path / "lpf.asc")
    names = {name for _, _, name in parse_asc(asc)["flags"]}
    assert {"p1", "p2", "0"} <= names, f"missing net labels; got {names}"


def test_every_shunt_element_reaches_ground(tmp_path) -> None:
    """A shunt cap wired only to the signal rail is a silent open circuit."""
    design = synthesize_lc_lpf("butterworth", order=5, cutoff_hz=1e9)
    asc = generate_lpf_asc(design.components, tmp_path / "lpf.asc")
    parsed = parse_asc(asc)
    pins = pin_positions(parsed["symbols"])
    ground_points = {(x, y) for x, y, name in parsed["flags"] if name == "0"}
    # Every C in a Butterworth ladder is a shunt element: one pin on ground.
    for inst, coords in pins.items():
        if inst.startswith("C"):
            assert any(c in ground_points for c in coords), (
                f"{inst} has no pin on a ground flag: {coords}"
            )


# ---------------------------------------------------------------------------
# End-to-end: synthesize -> .asc -> simulate -> extract -> compare to theory.
#
# The previous smoke tests asserted only that a .raw file existed, which is
# satisfied by a 692-byte stub containing zero data points. These assert the
# actual filter response against the closed-form doubly-terminated
# Butterworth transfer function, |S21|^2 = 1 / (1 + (f/fc)^(2n)).
# ---------------------------------------------------------------------------


def butterworth_s21_db(freq_hz: np.ndarray, fc_hz: float, order: int) -> np.ndarray:
    return -10.0 * np.log10(1.0 + (freq_hz / fc_hz) ** (2 * order))


def _run_and_compare(tmp_path: Path, simulator) -> None:
    from mcp_ltspice.extract import extract_sparams_from_raw
    from mcp_ltspice.runner import run_simulation

    fc, order = 1e9, 3
    design = synthesize_lc_lpf("butterworth", order=order, cutoff_hz=fc)
    asc = generate_lpf_asc(design.components, tmp_path / "lpf.asc")
    result = run_simulation(asc, prefer=simulator, timeout=240.0)
    net = extract_sparams_from_raw(result.raw_path, port_map={1: "p1", 2: "p2"}, z0=50.0)

    assert net.f.size > 100, f"expected a populated AC sweep, got {net.f.size} points"

    s21_db = 20.0 * np.log10(np.abs(net.s[:, 1, 0]))
    for probe_hz, tol_db in ((fc, 0.35), (2 * fc, 0.75)):
        i = int(np.argmin(np.abs(net.f - probe_hz)))
        expected = butterworth_s21_db(net.f[i], fc, order)
        assert abs(s21_db[i] - expected) < tol_db, (
            f"{simulator} |S21| at {net.f[i] / 1e9:.3f} GHz = {s21_db[i]:.2f} dB, "
            f"expected {expected:.2f} dB (tol {tol_db} dB)"
        )

    # Passband return loss: a correctly-terminated Butterworth is well matched
    # far below cutoff. A disconnected network would sit at |S11| ~ 0 dB.
    s11_db = 20.0 * np.log10(np.abs(net.s[:, 0, 0]))
    assert s11_db[0] < -20.0, f"expected good low-frequency match, got {s11_db[0]:.2f} dB"


@pytest.mark.ngspice
@pytest.mark.integration
def test_ngspice_end_to_end_matches_butterworth_theory(tmp_path) -> None:
    from mcp_ltspice.runner import Simulator

    _run_and_compare(tmp_path, Simulator.NGSPICE)


@pytest.mark.ltspice
@pytest.mark.integration
def test_ltspice_end_to_end_matches_butterworth_theory(tmp_path) -> None:
    from mcp_ltspice.runner import Simulator

    _run_and_compare(tmp_path, Simulator.LTSPICE)
