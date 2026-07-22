"""Qucs netlist generation.

Structural tests run everywhere; the ones marked ``qucs`` additionally
drive the real qucsator-RF binary and check the resulting S-parameters
against closed-form filter theory.
"""

from __future__ import annotations

import shutil
import subprocess

import numpy as np
import pytest

from mcp_qucs_s.netlist import generate_ladder_netlist
from mcp_qucs_s.sparams import network_from_dat

HAS_QUCS = shutil.which("qucsator_rf") is not None or shutil.which("qucsator") is not None
requires_qucs = pytest.mark.skipif(not HAS_QUCS, reason="qucsator not installed")

FC = 1e9
Z0 = 50.0
W = 2.0 * np.pi * FC

# 3rd-order Butterworth prototype, g = 1, 2, 1
LPF = [
    ("series_l", {"L": 1.0 * Z0 / W}),
    ("shunt_c", {"C": 2.0 / (Z0 * W)}),
    ("series_l", {"L": 1.0 * Z0 / W}),
]
# Highpass dual: series C, shunt L. The case a refdes-letter netlister
# gets wrong (see mcp-ltspice issue #32), so it is covered deliberately.
HPF = [
    ("series_c", {"C": 1.0 / (1.0 * Z0 * W)}),
    ("shunt_l", {"L": Z0 / (2.0 * W)}),
    ("series_c", {"C": 1.0 / (1.0 * Z0 * W)}),
]


# ---------------------------------------------------------------------------
# Structural
# ---------------------------------------------------------------------------


def test_ports_and_analysis_are_emitted(tmp_path) -> None:
    path = generate_ladder_netlist(LPF, tmp_path / "lpf.net")
    text = path.read_text()
    assert 'Pac:P1 _p1 gnd Num="1"' in text
    assert 'Num="2"' in text
    assert ".SP:SP1" in text


def test_series_elements_advance_the_node_and_shunts_do_not(tmp_path) -> None:
    text = generate_ladder_netlist(LPF, tmp_path / "lpf.net").read_text()
    lines = [ln for ln in text.splitlines() if ln.startswith(("L:", "C:"))]
    assert lines[0].startswith("L:L1 _p1 ")  # series from port 1
    assert " gnd " in lines[1]  # shunt cap to ground
    l1_out = lines[0].split()[2]
    assert lines[1].split()[1] == l1_out, "shunt must tap the node the series element produced"
    assert lines[2].split()[1] == l1_out, "second series element starts from that same node"


def test_port2_lands_on_the_final_node(tmp_path) -> None:
    text = generate_ladder_netlist(LPF, tmp_path / "lpf.net").read_text()
    last_series_out = next(ln for ln in text.splitlines() if ln.startswith("L:L3")).split()[2]
    p2 = next(ln for ln in text.splitlines() if 'Num="2"' in ln).split()[1]
    assert p2 == last_series_out


def test_all_shunt_ladder_puts_both_ports_on_one_node(tmp_path) -> None:
    """No series elements means port 1 and port 2 are the same node.

    Emitting a fictitious zero-ohm link instead would be a lie about the
    circuit; sharing the node is exact.
    """
    text = generate_ladder_netlist([("shunt_c", {"C": 1e-12})], tmp_path / "c.net").read_text()
    p1 = next(ln for ln in text.splitlines() if 'Num="1"' in ln).split()[1]
    p2 = next(ln for ln in text.splitlines() if 'Num="2"' in ln).split()[1]
    assert p1 == p2 == "_p1"


def test_trap_puts_only_the_cap_on_ground(tmp_path) -> None:
    text = generate_ladder_netlist(
        [("shunt_lc_trap", {"L": 5e-9, "C": 6e-12})], tmp_path / "t.net"
    ).read_text()
    lines = [ln for ln in text.splitlines() if ln.startswith(("L:", "C:"))]
    assert "gnd" not in lines[0], "trap inductor must not short to ground"
    assert lines[0].split()[2] == lines[1].split()[1], "L and C must share the mid node"
    assert lines[1].split()[2] == "gnd"


@pytest.mark.parametrize(
    ("bad", "match"),
    [
        ([], "no elements"),
        ([("series_l", {})], "missing required parameter"),
        ([("nonsense", {"L": 1e-9})], "Unknown ladder element"),
    ],
)
def test_invalid_input_is_rejected(tmp_path, bad, match) -> None:
    with pytest.raises(ValueError, match=match):
        generate_ladder_netlist(bad, tmp_path / "bad.net")


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"f_start_hz": 5e9, "f_stop_hz": 1e9}, "must exceed"),
        ({"points": 1}, "at least 2"),
        ({"z0": 0.0}, "must be positive"),
    ],
)
def test_invalid_sweep_settings_are_rejected(tmp_path, kwargs, match) -> None:
    with pytest.raises(ValueError, match=match):
        generate_ladder_netlist(LPF, tmp_path / "bad.net", **kwargs)


# ---------------------------------------------------------------------------
# Against the real engine
# ---------------------------------------------------------------------------


def _simulate(tmp_path, elements, name):
    net = generate_ladder_netlist(
        elements, tmp_path / f"{name}.net", f_start_hz=1e7, f_stop_hz=1e10, points=200
    )
    dat = tmp_path / f"{name}.dat"
    exe = shutil.which("qucsator_rf") or shutil.which("qucsator")
    proc = subprocess.run(
        [exe, "-i", str(net), "-o", str(dat)], capture_output=True, text=True, timeout=300
    )
    assert dat.is_file(), f"qucsator produced no output: {proc.stderr[-400:]}"
    return network_from_dat(dat)


@requires_qucs
@pytest.mark.integration
def test_lowpass_matches_butterworth_theory(tmp_path) -> None:
    net = _simulate(tmp_path, LPF, "lpf")
    s21_db = 20.0 * np.log10(np.abs(net.s[:, 1, 0]))
    expected = -10.0 * np.log10(1.0 + (net.f / FC) ** 6)
    assert np.max(np.abs(s21_db - expected)) < 0.05


@requires_qucs
@pytest.mark.integration
def test_highpass_matches_theory(tmp_path) -> None:
    """The dual network. A topology-inferring netlister gets this wrong."""
    net = _simulate(tmp_path, HPF, "hpf")
    s21_db = 20.0 * np.log10(np.abs(net.s[:, 1, 0]))
    expected = -10.0 * np.log10(1.0 + (FC / net.f) ** 6)
    assert np.max(np.abs(s21_db - expected)) < 0.05


@requires_qucs
@pytest.mark.integration
def test_trap_produces_a_notch(tmp_path) -> None:
    """A shunt series-LC shorts the line at its resonance."""
    l_h, c_f = 5e-9, 5e-12
    f0 = 1.0 / (2.0 * np.pi * np.sqrt(l_h * c_f))
    net = _simulate(
        tmp_path,
        [("series_l", {"L": 8e-9}), ("shunt_lc_trap", {"L": l_h, "C": c_f})],
        "trap",
    )
    s21_db = 20.0 * np.log10(np.abs(net.s[:, 1, 0]))
    notch_hz = net.f[int(np.argmin(s21_db))]
    assert notch_hz == pytest.approx(f0, rel=0.05), (
        f"notch at {notch_hz / 1e9:.3f} GHz, want {f0 / 1e9:.3f}"
    )
    assert s21_db.min() < -25.0, f"trap barely notches: {s21_db.min():.1f} dB"


@requires_qucs
@pytest.mark.integration
def test_simulate_lc_ladder_tool_end_to_end(tmp_path) -> None:
    """The MCP tool a user actually calls."""
    import mcp_qucs_s.server as S

    fn = getattr(S.simulate_lc_ladder, "fn", S.simulate_lc_ladder)
    out = fn(
        [
            {"kind": "series_l", "L": 1.0 * Z0 / W},
            {"kind": "shunt_c", "C": 2.0 / (Z0 * W)},
            {"kind": "series_l", "L": 1.0 * Z0 / W},
        ],
        str(tmp_path / "out.s2p"),
        f_start_hz=1e7,
        f_stop_hz=1e10,
        points=200,
    ).model_dump()

    assert out["status"] == "ok", out["error"]
    import skrf as rf

    net = rf.Network(out["data"]["s2p_path"])
    s21_db = 20.0 * np.log10(np.abs(net.s[:, 1, 0]))
    expected = -10.0 * np.log10(1.0 + (net.f / FC) ** 6)
    assert np.max(np.abs(s21_db - expected)) < 0.05


@requires_qucs
@pytest.mark.integration
def test_simulate_lc_ladder_tool_reports_bad_element(tmp_path) -> None:
    import mcp_qucs_s.server as S

    fn = getattr(S.simulate_lc_ladder, "fn", S.simulate_lc_ladder)
    out = fn([{"L": 1e-9}], str(tmp_path / "out.s2p")).model_dump()
    assert out["status"] == "error"
    assert "kind" in out["error"]
