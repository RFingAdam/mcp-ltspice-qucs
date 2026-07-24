"""Smoke tests for the FastMCP server: tool registration and envelope shape.

Tools that hit a simulator are exercised in ``test_runner.py``; here we
verify the MCP-level wiring (tools registered, names correct, envelope
contract honored) by calling the underlying tool functions directly.
"""

from __future__ import annotations

from pathlib import Path

from mcp_ltspice import server
from mcp_ltspice.runner import RunResult, Simulator


def test_server_instantiates() -> None:
    assert server.mcp.name == "mcp-ltspice"


def test_synthesize_tool_returns_ok_envelope(tmp_path) -> None:
    env = server.synthesize_lc_filter(
        filter_type="butterworth",
        order=3,
        cutoff_hz=1e9,
        output_asc=str(tmp_path / "lpf3.asc"),
        output_s2p=str(tmp_path / "lpf3.s2p"),
    )
    assert env.status == "ok"
    assert "components" in env.data
    assert Path(env.data["asc_path"]).exists()
    assert Path(env.data["s2p_path"]).exists()
    assert env.metadata["tool_version"]


def test_evaluate_filter_spec_tool_returns_ok(tmp_path) -> None:
    synth = server.synthesize_lc_filter(
        filter_type="butterworth",
        order=5,
        cutoff_hz=1e9,
        output_asc=str(tmp_path / "lpf5.asc"),
        output_s2p=str(tmp_path / "lpf5.s2p"),
    )
    spec = {
        "passband": {
            "f_start": 1e6,
            "f_stop": 500e6,
            "il_max_db": 0.5,
            "rl_min_db": 15,
        },
        "stopband_targets": [{"freq": 3e9, "rejection_min_db": 30, "label": "deep"}],
    }
    env = server.evaluate_filter_spec_tool(
        s2p_path=synth.data["s2p_path"],
        spec=spec,
    )
    assert env.status == "ok"
    assert env.data["overall"] == "pass"


def test_place_transmission_zero_tool_returns_ok(tmp_path) -> None:
    # Synthesize an elliptic LPF so we have an L2/C2 trap to move
    synth = server.synthesize_lc_filter(
        filter_type="elliptic",
        order=5,
        cutoff_hz=1e9,
        output_asc=str(tmp_path / "ellip5.asc"),
    )
    env = server.place_transmission_zero(
        asc_path=synth.data["asc_path"],
        trap_index=2,
        target_freq_hz=1.85e9,
        preserve_ratio=True,
        snap_series="E24",
    )
    assert env.status == "ok"
    assert env.data["asc_path"] != synth.data["asc_path"]
    assert Path(synth.data["asc_path"]).read_bytes() != Path(env.data["asc_path"]).read_bytes()
    assert env.data["target_freq_hz"] == 1.85e9
    # E24 snap should land within ~10%
    assert abs(env.data["freq_error_pct"]) < 10


def test_run_simulation_snapshots_input_and_requests_sandbox(tmp_path, monkeypatch) -> None:
    source = tmp_path / "main.cir"
    dependency = tmp_path / "part.lib"
    source.write_text('.include "part.lib"\nR1 in 0 50\n', encoding="utf-8")
    dependency.write_text(".model D D\n", encoding="utf-8")
    calls: list[tuple[Path, bool]] = []

    def fake_run(path, *, prefer, timeout, sandbox, cancel_requested):
        snapshot = Path(path)
        calls.append((snapshot, sandbox))
        raw = snapshot.with_suffix(".raw")
        log = snapshot.with_suffix(".log")
        raw.write_bytes(b"raw")
        log.write_text("ok", encoding="utf-8")
        return RunResult(raw, log, Simulator.NGSPICE, 0, "", "", sandboxed=sandbox)

    monkeypatch.setattr(server, "_run_simulation", fake_run)
    env = server.run_simulation(str(source), prefer="ngspice")

    assert env.status == "ok"
    snapshot, sandbox = calls[0]
    assert sandbox is True
    assert snapshot != source
    assert (snapshot.parent / "part.lib").read_bytes() == dependency.read_bytes()
    assert env.data["trusted_in_place"] is False


def test_durable_workspace_parse_and_analysis_job(tmp_path) -> None:
    workspace = server.workspace_create("test")
    assert workspace.status == "ok"
    workspace_id = workspace.data["workspace_id"]
    source = tmp_path / "simple.cir"
    source.write_text("R1 in 0 50\n.ac lin 3 1k 3k\n", encoding="utf-8")
    imported = server.artifact_import(workspace_id, str(source))
    assert imported.status == "ok"
    artifact_id = imported.data["artifact_id"]

    parsed = server.circuit_parse(workspace_id, artifact_id)
    assert parsed.status == "ok"
    assert parsed.data["format"] == "spice_netlist"
    assert parsed.data["is_supported"] is True
    assert parsed.data["components"][0]["pins"] == {"1": "in", "2": "0"}
    read = server.artifact_read(workspace_id, artifact_id)
    assert read.status == "ok"
    assert read.data["encoding"] == "base64"

    components = {"L1": 10e-9, "C2": 2e-12, "L3": 10e-9}
    spec = {
        "passband": {
            "f_start": 1e6,
            "f_stop": 100e6,
            "il_max_db": 3.0,
            "rl_min_db": 0.1,
        },
        "stopband_targets": [{"freq": 2e9, "rejection_min_db": 0.1, "label": "stop"}],
    }
    submitted = server.analysis_submit(
        "parameter_sweep",
        {
            "components": components,
            "sweep": {"L1": [10e-9]},
            "spec": spec,
        },
        workspace_id,
    )
    assert submitted.status == "ok"
    terminal = server._JOBS.wait(submitted.data["job_id"], timeout_sec=5)
    assert terminal["status"] == "completed"
    fetched = server.job_get(submitted.data["job_id"])
    assert fetched.data["result"]["n_points"] == 1


def test_render_response_tool_returns_ok(tmp_path) -> None:
    synth = server.synthesize_lc_filter(
        filter_type="butterworth",
        order=3,
        cutoff_hz=1e9,
        output_asc=str(tmp_path / "lpf3.asc"),
        output_s2p=str(tmp_path / "lpf3.s2p"),
    )
    env = server.render_response(
        s2p_path=synth.data["s2p_path"],
        output_png=str(tmp_path / "lpf3.png"),
        markers=[[500e6, "fc/2"], [1e9, "fc"]],
    )
    assert env.status == "ok"
    assert Path(env.data["png_path"]).exists()


def test_synthesize_with_invalid_args_returns_error_envelope(tmp_path) -> None:
    env = server.synthesize_lc_filter(
        filter_type="not_a_filter",
        order=3,
        cutoff_hz=1e9,
        output_asc=str(tmp_path / "x.asc"),
    )
    assert env.status == "error"
    assert env.error
