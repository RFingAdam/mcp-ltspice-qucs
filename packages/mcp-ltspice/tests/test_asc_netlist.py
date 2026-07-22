"""Geometry-driven netlisting of ``.asc`` schematics (issue #32).

The old netlister read each element's *position* off its refdes letter: a
lone ``C`` was assumed shunt, a lone ``L`` series. That describes a lowpass
ladder and nothing else, so a highpass (series C, shunt L) was silently
netlisted as its dual — ngspice ran without complaint and returned
S-parameters for a circuit nobody designed. ``z0`` was hardcoded to 50 Ω
besides, so a 75 Ω design simulated at 50 Ω.

Netlisting from geometry removes the guess: connectivity in a ``.asc`` *is*
coordinates, so the same rules LTspice applies give the right answer for any
topology, including schematics this package did not generate.
"""

from __future__ import annotations

import shutil

import numpy as np
import pytest

from mcp_ltspice.asc_io import (
    _asc_header,
    _attr,
    _flag,
    _place_cap,
    _place_series,
    _place_vertical,
    _wire,
    series_span,
    to_ltspice_value,
)
from mcp_ltspice.asc_netlist import netlist_from_asc

HAS_NGSPICE = shutil.which("ngspice") is not None
requires_ngspice = pytest.mark.skipif(not HAS_NGSPICE, reason="ngspice not installed")

FC = 1e9
W = 2.0 * np.pi * FC
GAP = 64
Y = 144


def build_ladder_asc(path, elements, *, z0=50.0, ac=".ac dec 200 1e7 1e10"):
    """Lay out a ladder using the same placement helpers as the generator.

    Written in the test rather than production code because production only
    needs to emit lowpass ladders today; this exercises the netlister against
    topologies the generator cannot yet produce.
    """
    lines = _asc_header()
    flags = []
    x = 96

    lines.extend(_place_vertical("voltage", x, Y))
    lines.append(_attr("InstName", "V1"))
    lines.append(_attr("Value", "AC 1 0"))
    flags.append(_flag(x, Y + 80, "0"))

    lines.append(_wire(x, Y, x + GAP, Y))
    x += GAP
    lines.extend(_place_series("res", x, Y))
    lines.append(_attr("InstName", "Rs1"))
    lines.append(_attr("Value", to_ltspice_value(z0)))
    x += series_span("res")
    flags.append(_flag(x, Y, "p1"))

    for i, (kind, value) in enumerate(elements, start=1):
        lines.append(_wire(x, Y, x + GAP, Y))
        x += GAP
        if kind == "series_c":
            lines.extend(_place_series("cap", x, Y))
            lines.append(_attr("InstName", f"C{i}"))
            lines.append(_attr("Value", to_ltspice_value(value)))
            x += series_span("cap")
        elif kind == "series_l":
            lines.extend(_place_series("ind", x, Y))
            lines.append(_attr("InstName", f"L{i}"))
            lines.append(_attr("Value", to_ltspice_value(value)))
            x += series_span("ind")
        elif kind == "shunt_l":
            lines.extend(_place_vertical("ind", x, Y))
            lines.append(_attr("InstName", f"L{i}"))
            lines.append(_attr("Value", to_ltspice_value(value)))
            flags.append(_flag(x, Y + 80, "0"))
        elif kind == "shunt_c":
            lines.extend(_place_cap(x, Y))
            lines.append(_attr("InstName", f"C{i}"))
            lines.append(_attr("Value", to_ltspice_value(value)))
            flags.append(_flag(x, Y + 64, "0"))
        else:
            raise AssertionError(f"unhandled kind {kind}")

    lines.append(_wire(x, Y, x + GAP, Y))
    x += GAP
    lines.extend(_place_vertical("res", x, Y))
    lines.append(_attr("InstName", "RL1"))
    lines.append(_attr("Value", to_ltspice_value(z0)))
    flags.append(_flag(x, Y, "p2"))
    flags.append(_flag(x, Y + 80, "0"))

    lines.append(f"TEXT 96 480 Left 2 !{ac}")
    lines.extend(flags)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# 3rd-order Butterworth highpass, fc = 1 GHz: series C, shunt L, series C
HPF = [
    ("series_c", 1.0 / (1.0 * 50.0 * W)),
    ("shunt_l", 50.0 / (2.0 * W)),
    ("series_c", 1.0 / (1.0 * 50.0 * W)),
]


def _elements(netlist: str) -> dict[str, tuple[str, str]]:
    """{refdes: (node_a, node_b)} for the netlist body."""
    out = {}
    for line in netlist.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0][0] in "RLCV" and not line.startswith("*"):
            out[parts[0]] = (parts[1], parts[2])
    return out


def test_series_capacitor_stays_in_the_main_path(tmp_path) -> None:
    """The #32 regression: a series C must not be netlisted to ground."""
    asc = build_ladder_asc(tmp_path / "hpf.asc", HPF)
    netlist, _ = netlist_from_asc(asc)
    els = _elements(netlist)

    for ref in ("C1", "C3"):
        assert "0" not in els[ref], f"{ref} was netlisted as a shunt: {els[ref]}"
    assert "0" in els["L2"], f"shunt inductor L2 should reach ground: {els['L2']}"


def test_series_capacitors_are_in_series_with_each_other(tmp_path) -> None:
    """C1 -> L2 tap -> C3 must form a chain from p1 to p2."""
    asc = build_ladder_asc(tmp_path / "hpf.asc", HPF)
    netlist, _ = netlist_from_asc(asc)
    els = _elements(netlist)
    mid = set(els["C1"]) & set(els["C3"])
    assert mid, f"C1 {els['C1']} and C3 {els['C3']} share no node"
    assert mid & set(els["L2"]), "the shunt inductor should tap the node between them"
    assert "p1" in els["C1"] or "p1" in els["C3"]
    assert "p2" in els["C1"] or "p2" in els["C3"]


def test_lowpass_still_netlists_correctly(tmp_path) -> None:
    lpf = [("series_l", 50.0 / W), ("shunt_c", 2.0 / (50.0 * W)), ("series_l", 50.0 / W)]
    asc = build_ladder_asc(tmp_path / "lpf.asc", lpf)
    netlist, _ = netlist_from_asc(asc)
    els = _elements(netlist)
    assert "0" in els["C2"], "shunt cap should reach ground"
    for ref in ("L1", "L3"):
        assert "0" not in els[ref], f"{ref} should be in the main path"


@pytest.mark.parametrize("z0", [50.0, 75.0])
def test_z0_is_read_from_the_schematic(tmp_path, z0: float) -> None:
    """It used to be hardcoded to 50, so a 75 ohm design simulated at 50."""
    asc = build_ladder_asc(tmp_path / f"z{z0:.0f}.asc", HPF, z0=z0)
    _, parsed_z0 = netlist_from_asc(asc)
    assert parsed_z0 == pytest.approx(z0)


def test_ac_directive_is_carried_through(tmp_path) -> None:
    asc = build_ladder_asc(tmp_path / "a.asc", HPF, ac=".ac dec 33 1e8 2e9")
    netlist, _ = netlist_from_asc(asc)
    assert ".ac dec 33 1e8 2e9" in netlist


def test_missing_port_flag_is_reported(tmp_path) -> None:
    asc = build_ladder_asc(tmp_path / "b.asc", HPF)
    kept = [ln for ln in asc.read_text().splitlines() if not ln.endswith(" p2")]
    assert len(kept) < len(asc.read_text().splitlines()), "no p2 flag was present to remove"
    asc.write_text("\n".join(kept) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="p2"):
        netlist_from_asc(asc)


def test_unknown_symbol_kind_raises_rather_than_guessing(tmp_path) -> None:
    asc = tmp_path / "odd.asc"
    asc.write_text(
        "Version 4\nSHEET 1 1280 720\n"
        "SYMBOL nmos4 96 144 R0\nSYMATTR InstName M1\nSYMATTR Value FDS6679\n"
        "FLAG 96 144 p1\nFLAG 200 144 p2\n",
        encoding="utf-8",
    )
    with pytest.raises(NotImplementedError, match="nmos4"):
        netlist_from_asc(asc)


# ---------------------------------------------------------------------------
# Against real ngspice
# ---------------------------------------------------------------------------


def _simulate(tmp_path, asc):
    from mcp_ltspice.extract import extract_sparams_from_raw
    from mcp_ltspice.runner import Simulator, run_simulation

    result = run_simulation(asc, prefer=Simulator.NGSPICE, timeout=240.0)
    return extract_sparams_from_raw(result.raw_path, port_map={1: "p1", 2: "p2"})


@requires_ngspice
@pytest.mark.ngspice
@pytest.mark.integration
def test_highpass_simulates_as_a_highpass(tmp_path) -> None:
    """End-to-end proof of #32: the dual network would fail this badly."""
    asc = build_ladder_asc(tmp_path / "hpf.asc", HPF)
    net = _simulate(tmp_path, asc)
    s21_db = 20.0 * np.log10(np.abs(net.s[:, 1, 0]))
    expected = -10.0 * np.log10(1.0 + (FC / net.f) ** 6)
    worst = float(np.max(np.abs(s21_db - expected)))
    assert worst < 0.2, f"|S21| deviates from highpass theory by {worst:.2f} dB"

    # Sanity: it must actually rise with frequency, not fall.
    assert s21_db[-1] > s21_db[0] + 20.0


@requires_ngspice
@pytest.mark.ngspice
@pytest.mark.integration
def test_75_ohm_design_simulates_at_75_ohm(tmp_path) -> None:
    """A 75 ohm ladder is only well matched when simulated at 75 ohm."""
    z0 = 75.0
    lpf = [
        ("series_l", 1.0 * z0 / W),
        ("shunt_c", 2.0 / (z0 * W)),
        ("series_l", 1.0 * z0 / W),
    ]
    asc = build_ladder_asc(tmp_path / "lpf75.asc", lpf, z0=z0)

    from mcp_ltspice.extract import extract_sparams_from_raw
    from mcp_ltspice.runner import Simulator, run_simulation

    _, parsed_z0 = netlist_from_asc(asc)
    assert parsed_z0 == pytest.approx(75.0)

    result = run_simulation(asc, prefer=Simulator.NGSPICE, timeout=240.0)
    net = extract_sparams_from_raw(result.raw_path, port_map={1: "p1", 2: "p2"}, z0=parsed_z0)
    s11_db = 20.0 * np.log10(np.abs(net.s[:, 0, 0]))
    assert s11_db[0] < -30.0, (
        f"75 ohm design should be well matched at 75 ohm; got {s11_db[0]:.1f} dB. "
        "Simulating it at 50 ohm would leave a visible mismatch."
    )
