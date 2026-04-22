"""Smoke tests for the FastMCP server: tool registration and envelope shape.

Tools that hit a simulator are exercised in ``test_runner.py``; here we
verify the MCP-level wiring (tools registered, names correct, envelope
contract honored) by calling the underlying tool functions directly.
"""

from __future__ import annotations

from pathlib import Path

from mcp_ltspice import server


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
            "f_start": 1e6, "f_stop": 500e6, "il_max_db": 0.5, "rl_min_db": 15,
        },
        "stopband_targets": [{"freq": 3e9, "rejection_min_db": 30, "label": "deep"}],
    }
    env = server.evaluate_filter_spec_tool(
        s2p_path=synth.data["s2p_path"], spec=spec,
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
    assert env.data["target_freq_hz"] == 1.85e9
    # E24 snap should land within ~10%
    assert abs(env.data["freq_error_pct"]) < 10


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
