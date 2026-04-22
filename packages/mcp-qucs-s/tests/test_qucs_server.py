"""Smoke tests for the mcp-qucs-s server tool surface."""

from __future__ import annotations

from mcp_qucs_s import server


def test_status_tool_returns_capability_info() -> None:
    env = server.status()
    assert env.status == "ok"
    assert "qucs_s_available" in env.data
    assert "xyce_available" in env.data
    assert isinstance(env.data["qucs_s_available"], bool)


def test_synthesize_microstrip_line_tool_returns_dimensions() -> None:
    env = server.synthesize_microstrip_line(
        z0_ohm=50.0,
        electrical_length_deg=90.0,
        freq_hz=915e6,
        substrate={"er": 4.4, "h_mm": 0.254},
    )
    assert env.status == "ok"
    assert env.data["width_mm"] > 0
    assert env.data["length_mm"] > 0


def test_analyze_microstrip_tool() -> None:
    env = server.analyze_microstrip_tool(
        width_mm=0.5,
        substrate={"er": 4.4, "h_mm": 0.254},
    )
    assert env.status == "ok"
    assert env.data["z0_ohm"] > 0
    assert env.data["er_eff"] > 1.0


def test_synthesize_coupler_tool_branch_line() -> None:
    env = server.synthesize_coupler(
        kind="branch_line",
        coupling_db=3.0,
        freq_hz=2.4e9,
        z0_ohm=50.0,
        substrate={"er": 4.4, "h_mm": 0.254},
    )
    assert env.status == "ok"
    assert env.data["kind"] == "branch_line"
    assert len(env.data["sections"]) == 4


def test_lumped_to_distributed_tool() -> None:
    env = server.lumped_to_distributed(
        components={"L1": 5e-9, "C2": 2e-12, "L3": 5e-9},
        cutoff_hz=1e9,
        substrate={"er": 4.4, "h_mm": 0.254},
    )
    assert env.status == "ok"
    assert env.data["n_elements"] >= 3


def test_run_sp_analysis_returns_error_when_qucs_missing(monkeypatch) -> None:
    monkeypatch.setattr("mcp_qucs_s.server.is_qucs_available", lambda: False)
    env = server.run_sp_analysis(sch_path="/nope.sch", output_s2p="/nope.s2p")
    assert env.status == "error"
    assert "Qucs-S" in env.error


def test_run_harmonic_balance_returns_error_when_xyce_missing(monkeypatch) -> None:
    monkeypatch.setattr("mcp_qucs_s.server.is_xyce_available", lambda: False)
    env = server.run_harmonic_balance(
        sch_path="/nope.sch",
        fundamentals_hz=[1e9],
    )
    assert env.status == "error"
    assert "Xyce" in env.error
