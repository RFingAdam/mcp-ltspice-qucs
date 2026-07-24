"""Regression coverage for the explicit two-excitation S-parameter path."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from mcp_ltspice.asc_io import generate_lpf_asc, generate_port2_excitation_asc
from mcp_ltspice.asc_netlist import netlist_from_asc
from mcp_ltspice.extract import extract_two_sweep_sparams, ladder_sparams_from_components


class _Trace:
    def __init__(self, wave: np.ndarray) -> None:
        self.wave = wave

    def get_wave(self) -> np.ndarray:
        return self.wave


class _Raw:
    def __init__(self, traces: dict[str, np.ndarray]) -> None:
        self.traces = traces
        self._trace_info: list[object] = []

    def get_trace(self, name: str) -> _Trace | None:
        wave = self.traces.get(name)
        return _Trace(wave) if wave is not None else None


def _sweep(
    freq: np.ndarray,
    *,
    driven_port: int,
    reflection: complex,
    transmission: complex,
) -> _Raw:
    # For the declared 1 V / Z0 fixture:
    #   Sii = 2*V(driven)-1, Sji = 2*V(other).
    v_driven = np.full(freq.size, (reflection + 1.0) / 2.0, dtype=np.complex128)
    v_other = np.full(freq.size, transmission / 2.0, dtype=np.complex128)
    return _Raw(
        {
            "frequency": freq.astype(np.complex128),
            "V(p1)": v_driven if driven_port == 1 else v_other,
            "V(p2)": v_other if driven_port == 1 else v_driven,
        }
    )


def test_two_sweeps_recover_all_four_independent_terms(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_ltspice import extract as extract_module

    freq = np.array([1e6, 2e6, 3e6])
    raws = {
        "port1.raw": _sweep(freq, driven_port=1, reflection=0.2 + 0.1j, transmission=2.0),
        "port2.raw": _sweep(freq, driven_port=2, reflection=-0.4, transmission=0.03j),
    }
    monkeypatch.setattr(
        extract_module,
        "_open_raw",
        lambda path, dialect=None: raws[Path(path).name],
    )

    net, provenance = extract_two_sweep_sparams(
        "port1.raw",
        "port2.raw",
        port_map={1: "p1", 2: "p2"},
    )

    assert np.allclose(net.s[:, 0, 0], 0.2 + 0.1j)
    assert np.allclose(net.s[:, 1, 0], 2.0)
    assert np.allclose(net.s[:, 0, 1], 0.03j)
    assert np.allclose(net.s[:, 1, 1], -0.4)
    assert not np.allclose(net.s[:, 0, 1], net.s[:, 1, 0])
    assert not np.allclose(net.s[:, 1, 1], net.s[:, 0, 0])
    assert provenance["extraction_method"] == "two_excitation_power_waves"


def test_missing_port2_trace_fails_instead_of_emitting_zeros(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_ltspice import extract as extract_module

    freq = np.array([1e6, 2e6])
    port1 = _sweep(freq, driven_port=1, reflection=0.0, transmission=1.0)
    port2 = _Raw(
        {
            "frequency": freq.astype(np.complex128),
            "V(p2)": np.full(freq.size, 0.5, dtype=np.complex128),
        }
    )
    raws = {"port1.raw": port1, "port2.raw": port2}
    monkeypatch.setattr(
        extract_module,
        "_open_raw",
        lambda path, dialect=None: raws[Path(path).name],
    )

    with pytest.raises(ValueError, match=r"V\(p1\)"):
        extract_two_sweep_sparams(
            "port1.raw",
            "port2.raw",
            port_map={1: "p1", 2: "p2"},
        )


def test_mismatched_sweep_grids_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_ltspice import extract as extract_module

    raws = {
        "port1.raw": _sweep(np.array([1e6, 2e6]), driven_port=1, reflection=0.0, transmission=1.0),
        "port2.raw": _sweep(
            np.array([1e6, 2.1e6]), driven_port=2, reflection=0.0, transmission=1.0
        ),
    }
    monkeypatch.setattr(
        extract_module,
        "_open_raw",
        lambda path, dialect=None: raws[Path(path).name],
    )

    with pytest.raises(ValueError, match="different frequency grids"):
        extract_two_sweep_sparams(
            "port1.raw",
            "port2.raw",
            port_map={1: "p1", 2: "p2"},
        )


def test_port2_fixture_generation_reverses_source_without_mutating_input(
    tmp_path: Path,
) -> None:
    source = generate_lpf_asc({"L1": 10e-9, "C2": 2e-12}, tmp_path / "source.asc")
    original = source.read_bytes()
    port2 = generate_port2_excitation_asc(source, tmp_path / "port2.asc", z0=50.0)

    assert source.read_bytes() == original
    netlist, parsed_z0 = netlist_from_asc(port2)
    assert parsed_z0 == 50.0
    assert "V1 " in netlist and " AC 0 0" in netlist
    assert "V2 " in netlist and " AC 1 0" in netlist
    assert "Rs1" in netlist and "p1" in netlist
    assert "RL1" in netlist and "p2" in netlist


@pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")
@pytest.mark.ngspice
@pytest.mark.integration
def test_ngspice_two_sweep_matches_asymmetric_reciprocal_reference(tmp_path: Path) -> None:
    from mcp_ltspice.server import extract_sparameters

    components = {"L1": 10e-9, "C2": 2e-12}
    asc = generate_lpf_asc(
        components,
        tmp_path / "asymmetric.asc",
        f_start_hz=1e7,
        f_stop_hz=3e9,
        npoints_per_decade=50,
    )
    result = extract_sparameters(
        asc_path=str(asc),
        output_s2p=str(tmp_path / "asymmetric.s2p"),
        prefer="ngspice",
    )
    assert result.status == "ok", result.error

    import skrf as rf

    measured = rf.Network(result.data["s2p_path"])
    expected = ladder_sparams_from_components(
        [("series_l", {"L": components["L1"]}), ("shunt_c", {"C": components["C2"]})],
        measured.f,
        z0=50.0,
    )
    assert np.max(np.abs(measured.s - expected)) < 2e-5
    assert np.max(np.abs(measured.s[:, 0, 0] - measured.s[:, 1, 1])) > 0.01
    assert result.data["provenance"]["extraction_method"] == "two_excitation_power_waves"
