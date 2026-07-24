from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from mcp_ltspice import capabilities as spice
from mcp_ltspice.runner import Simulator
from mcp_qucs_s import capabilities as qucs
from mcp_rf_analysis.capabilities import analysis_capabilities


def test_rf_analysis_known_answer_is_validated() -> None:
    result = analysis_capabilities()

    assert result["state"] == "validated"
    assert result["validated"] is True
    assert result["sandbox_profile"]["available"] is True


def test_spice_probe_distinguishes_missing_backend(monkeypatch: object) -> None:
    monkeypatch.setattr(spice, "find_ngspice", lambda: None)  # type: ignore[attr-defined]

    result = spice.probe_spice_backend("ngspice")

    assert result["state"] == "unavailable"
    assert result["installed"] is False
    assert result["launchable"] is False


def test_spice_probe_validates_known_answer(monkeypatch: object, tmp_path: Path) -> None:
    executable = tmp_path / "ngspice"
    executable.write_text("", encoding="utf-8")
    raw = tmp_path / "answer.raw"
    raw.write_bytes(b"raw")
    monkeypatch.setattr(spice, "find_ngspice", lambda: executable)  # type: ignore[attr-defined]
    monkeypatch.setattr(spice, "probe_executable_version", lambda *a, **k: "1.0")  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        spice,
        "run_simulation",
        lambda *a, **k: SimpleNamespace(raw_path=raw, simulator=Simulator.NGSPICE),
    )

    result = spice.probe_spice_backend("ngspice")

    assert result["state"] == "validated"
    assert result["version"] == "1.0"


def test_qucs_probe_distinguishes_installed_from_launchable(
    monkeypatch: object, tmp_path: Path
) -> None:
    executable = tmp_path / "qucsator"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(qucs, "find_qucs_s", lambda: executable)  # type: ignore[attr-defined]
    monkeypatch.setattr(qucs, "_version", lambda *a, **k: (False, None))  # type: ignore[attr-defined]

    result = qucs.probe_qucs_backend("qucsator")

    assert result["installed"] is True
    assert result["launchable"] is False
    assert result["state"] == "installed"


def test_capability_probes_reject_unknown_backends() -> None:
    for probe in (spice.probe_spice_backend, qucs.probe_qucs_backend):
        try:
            probe("not-a-backend")
        except ValueError:
            pass
        else:
            raise AssertionError("unknown backend was accepted")
