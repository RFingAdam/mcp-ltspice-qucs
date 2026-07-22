"""Tests for the simulator runner.

Real simulator integration tests are gated by ``ngspice`` and ``ltspice``
markers (see ``conftest.py``); when neither simulator is installed the
runner-detection tests still run to verify the auto-discovery logic.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from mcp_ltspice.asc_io import generate_lpf_asc
from mcp_ltspice.runner import (
    SIMULATOR_ENV_VAR,
    Simulator,
    _check_produced_artifact,
    _needs_wine,
    _refdes_index,
    _to_wine_path,
    detect_simulator,
    find_ltspice,
    find_ngspice,
    find_wine,
    run_simulation,
    simulator_from_env,
)
from mcp_ltspice.runner import log as runner_log
from mcp_ltspice.synthesis import synthesize_lc_lpf


def _completed(returncode: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["sim"], returncode=returncode, stdout="", stderr="")


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


# ---------------------------------------------------------------------------
# Success policy: the .raw artifact decides, not the return code.
#
# LTspice under Wine exits 1 on successful runs (reported downstream by
# @cr4i50n), so a strict rc check made the server unusable there. These
# tests pin both halves of the policy so it can't silently regress back.
# ---------------------------------------------------------------------------


def test_nonzero_returncode_with_raw_file_is_success(tmp_path) -> None:
    """rc=1 but the .raw exists — this is the Wine case, and it must pass."""
    raw = tmp_path / "sim.raw"
    raw.write_text("stub", encoding="utf-8")
    _check_produced_artifact(
        raw,
        _completed(1),
        simulator="LTspice",
        log_path=tmp_path / "sim.log",
        cmd=["wine", "LTspice.exe"],
    )


def test_zero_returncode_without_raw_file_is_failure(tmp_path) -> None:
    """rc=0 but nothing was produced — still a failure, not a silent pass."""
    with pytest.raises(RuntimeError, match="did not produce"):
        _check_produced_artifact(
            tmp_path / "missing.raw",
            _completed(0),
            simulator="ngspice",
            log_path=tmp_path / "sim.log",
            cmd=["ngspice", "-b"],
        )


def test_failure_message_carries_returncode_and_command(tmp_path) -> None:
    """The diagnostic must survive: rc and the exact command both appear."""
    with pytest.raises(RuntimeError) as exc:
        _check_produced_artifact(
            tmp_path / "missing.raw",
            _completed(3),
            simulator="ngspice",
            log_path=tmp_path / "sim.log",
            cmd=["ngspice", "-b", "netlist.cir"],
        )
    assert "returncode=3" in str(exc.value)
    assert "netlist.cir" in str(exc.value)


# ---------------------------------------------------------------------------
# Wine invocation
# ---------------------------------------------------------------------------


def test_needs_wine_only_off_windows(monkeypatch) -> None:
    """A .exe runs directly on Windows; Wine is only needed elsewhere."""
    monkeypatch.setattr("mcp_ltspice.runner.os.name", "posix")
    assert _needs_wine(Path("/x/LTspice.exe")) is True
    monkeypatch.setattr("mcp_ltspice.runner.os.name", "nt")
    assert _needs_wine(Path("C:/x/LTspice.exe")) is False


def test_needs_wine_false_for_native_binary(monkeypatch) -> None:
    monkeypatch.setattr("mcp_ltspice.runner.os.name", "posix")
    assert _needs_wine(Path("/Applications/LTspice.app/Contents/MacOS/LTspice")) is False


def test_to_wine_path_translates_via_winepath(monkeypatch) -> None:
    monkeypatch.setattr(
        "mcp_ltspice.runner.subprocess.check_output",
        lambda *a, **k: "Z:\\home\\u\\lpf.asc\n",
    )
    assert _to_wine_path(Path("/usr/bin/wine"), Path("/home/u/lpf.asc")) == "Z:\\home\\u\\lpf.asc"


def test_to_wine_path_falls_back_when_winepath_unavailable(monkeypatch) -> None:
    """A missing/broken winepath must not abort the run."""

    def _boom(*a, **k):
        raise FileNotFoundError("winepath")

    monkeypatch.setattr("mcp_ltspice.runner.subprocess.check_output", _boom)
    assert _to_wine_path(Path("/usr/bin/wine"), Path("/home/u/lpf.asc")) == "/home/u/lpf.asc"


# ---------------------------------------------------------------------------
# LTspice discovery
# ---------------------------------------------------------------------------


def test_ltspice_path_env_var_takes_precedence(tmp_path, monkeypatch) -> None:
    exe = tmp_path / "LTspice.exe"
    exe.write_text("stub", encoding="utf-8")
    monkeypatch.setenv("LTSPICE_PATH", str(exe))
    assert find_ltspice() == exe


def test_bogus_ltspice_path_falls_through_and_warns(tmp_path, monkeypatch, caplog) -> None:
    """A typo'd LTSPICE_PATH must not be returned, and must not be silent.

    These loggers set propagate=False and bind their stream at import
    time, so neither caplog nor capsys/capfd sees them by default. Flip
    propagation on for the duration so the record reaches caplog.
    """
    monkeypatch.setattr(runner_log, "propagate", True)
    monkeypatch.setenv("LTSPICE_PATH", str(tmp_path / "nope.exe"))
    monkeypatch.setattr("mcp_ltspice.runner.shutil.which", lambda _c: None)
    monkeypatch.setattr("mcp_ltspice.runner.Path.home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("WINEPREFIX", raising=False)
    with caplog.at_level(logging.WARNING, logger="mcp_ltspice.runner"):
        assert find_ltspice() is None
    assert "LTSPICE_PATH" in caplog.text


def test_find_ltspice_discovers_wine_prefix_install(tmp_path, monkeypatch) -> None:
    """WINEPREFIX installs are found even when nothing is on $PATH."""
    exe = tmp_path / "drive_c/Program Files/ADI/LTspice/LTspice.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("stub", encoding="utf-8")
    monkeypatch.delenv("LTSPICE_PATH", raising=False)
    monkeypatch.setenv("WINEPREFIX", str(tmp_path))
    monkeypatch.setattr("mcp_ltspice.runner.shutil.which", lambda _c: None)
    assert find_ltspice() == exe


# ---------------------------------------------------------------------------
# MCP_LTSPICE_SIMULATOR pin
# ---------------------------------------------------------------------------


def test_simulator_from_env_unset_is_none(monkeypatch) -> None:
    monkeypatch.delenv(SIMULATOR_ENV_VAR, raising=False)
    assert simulator_from_env() is None


@pytest.mark.parametrize("value", ["ngspice", "NGSPICE", "  ngspice  "])
def test_simulator_from_env_parses_value(monkeypatch, value) -> None:
    monkeypatch.setenv(SIMULATOR_ENV_VAR, value)
    assert simulator_from_env() == Simulator.NGSPICE


def test_simulator_from_env_rejects_unknown_value(monkeypatch) -> None:
    """A typo must fail loudly rather than silently picking a simulator."""
    monkeypatch.setenv(SIMULATOR_ENV_VAR, "spice3")
    with pytest.raises(RuntimeError, match="not a known simulator"):
        simulator_from_env()


def test_detect_simulator_honours_pin(monkeypatch) -> None:
    """Pinning ngspice must not report LTspice even when LTspice exists."""
    monkeypatch.setattr("mcp_ltspice.runner.find_ltspice", lambda: Path("/x/LTspice.exe"))
    monkeypatch.setattr("mcp_ltspice.runner.find_ngspice", lambda: Path("/usr/bin/ngspice"))
    monkeypatch.setenv(SIMULATOR_ENV_VAR, "ngspice")
    assert detect_simulator() == Simulator.NGSPICE


def test_detect_simulator_pin_to_missing_binary_is_none(monkeypatch) -> None:
    """Pinning something absent reports None, not a silent fallback."""
    monkeypatch.setattr("mcp_ltspice.runner.find_ltspice", lambda: None)
    monkeypatch.setattr("mcp_ltspice.runner.find_ngspice", lambda: Path("/usr/bin/ngspice"))
    monkeypatch.setenv(SIMULATOR_ENV_VAR, "ltspice")
    assert detect_simulator() is None


def test_find_ltspice_stays_truthful_under_pin(monkeypatch, tmp_path) -> None:
    """Discovery reports what's installed regardless of the selection pin.

    Guards the layering: an earlier proposal short-circuited find_ltspice()
    on this env var, which made run_simulation(prefer="ltspice") report
    "LTspice not found" on machines where it was installed.
    """
    exe = tmp_path / "LTspice.exe"
    exe.write_text("stub", encoding="utf-8")
    monkeypatch.setenv("LTSPICE_PATH", str(exe))
    monkeypatch.setenv(SIMULATOR_ENV_VAR, "ngspice")
    assert find_ltspice() == exe


def test_run_simulation_env_pin_selects_ngspice(tmp_path, monkeypatch) -> None:
    """The pin routes run_simulation even though LTspice is 'installed'."""
    called: list[str] = []
    monkeypatch.setattr("mcp_ltspice.runner.find_ltspice", lambda: Path("/x/LTspice.exe"))
    monkeypatch.setattr("mcp_ltspice.runner.find_ngspice", lambda: Path("/usr/bin/ngspice"))
    monkeypatch.setattr(
        "mcp_ltspice.runner._run_ngspice",
        lambda *a, **k: called.append("ngspice"),
    )
    monkeypatch.setenv(SIMULATOR_ENV_VAR, "ngspice")
    design = synthesize_lc_lpf("butterworth", order=3, cutoff_hz=1e9)
    asc = generate_lpf_asc(design.components, tmp_path / "lpf.asc")
    run_simulation(asc)
    assert called == ["ngspice"]


# ---------------------------------------------------------------------------
# Netlist refdes ordering
# ---------------------------------------------------------------------------


def test_refdes_index_parses_and_rejects() -> None:
    assert _refdes_index("L12") == 12
    with pytest.raises(ValueError, match="no numeric index"):
        _refdes_index("Lout")
