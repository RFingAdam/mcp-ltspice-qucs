"""Tests for the simulator runner.

Real simulator integration tests are gated by ``ngspice`` and ``ltspice``
markers (see ``conftest.py``); when neither simulator is installed the
runner-detection tests still run to verify the auto-discovery logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp_ltspice.asc_io import generate_lpf_asc
from mcp_ltspice.runner import (
    Simulator,
    detect_simulator,
    find_ltspice,
    find_ngspice,
    find_wine,
    run_simulation,
)
from mcp_ltspice.synthesis import synthesize_lc_lpf


def test_find_helpers_return_path_or_none() -> None:
    for fn in (find_ltspice, find_ngspice, find_wine):
        result = fn()
        assert result is None or isinstance(result, Path)


def test_detect_simulator_returns_known_value_or_none() -> None:
    sim = detect_simulator()
    assert sim is None or sim in (Simulator.LTSPICE, Simulator.NGSPICE)


def test_run_simulation_missing_asc_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        run_simulation(tmp_path / "no.asc")


def test_run_simulation_no_simulator_raises(tmp_path, monkeypatch) -> None:
    """If both simulators are absent, the runner errors out clearly."""
    monkeypatch.setattr("mcp_ltspice.runner.find_ltspice", lambda: None)
    monkeypatch.setattr("mcp_ltspice.runner.find_ngspice", lambda: None)
    design = synthesize_lc_lpf("butterworth", order=3, cutoff_hz=1e9)
    asc = generate_lpf_asc(design.components, tmp_path / "lpf.asc")
    with pytest.raises(RuntimeError, match="No SPICE simulator found"):
        run_simulation(asc)


@pytest.mark.ngspice
@pytest.mark.integration
def test_run_simulation_ngspice_smoke(tmp_path) -> None:
    """End-to-end: synthesize → asc → ngspice → raw file exists."""
    design = synthesize_lc_lpf("butterworth", order=3, cutoff_hz=1e9)
    asc = generate_lpf_asc(design.components, tmp_path / "lpf.asc")
    result = run_simulation(asc, prefer=Simulator.NGSPICE)
    assert result.simulator == Simulator.NGSPICE
    assert result.raw_path.is_file()


@pytest.mark.ltspice
@pytest.mark.integration
def test_run_simulation_ltspice_smoke(tmp_path) -> None:
    design = synthesize_lc_lpf("butterworth", order=3, cutoff_hz=1e9)
    asc = generate_lpf_asc(design.components, tmp_path / "lpf.asc")
    result = run_simulation(asc, prefer=Simulator.LTSPICE)
    assert result.simulator == Simulator.LTSPICE
    assert result.raw_path.is_file()
