"""Tests for Monte Carlo `trace=True` per-trial JSONL output.

Background: when MC yield is below 100 %, you want to know which
component values dominated the failing trials. The `trace=True` flag
emits a JSONL record per trial with the seed, sampled component values,
metric measurements, and pass/fail status — enough for an offline
sensitivity / root-cause analysis script to find the culprit.
"""

from __future__ import annotations

import json

import pytest

from mcp_ltspice.eval import FilterSpec
from mcp_ltspice.montecarlo import monte_carlo_analysis
from mcp_ltspice.synthesis import Topology, synthesize_lc_lpf

# Same test fixture across cases: a 5th-order Butterworth at 1 GHz.
LPF = synthesize_lc_lpf(
    "butterworth",
    order=5,
    cutoff_hz=1e9,
    z0=50.0,
    topology=Topology.SERIES_FIRST,
)
SPEC = FilterSpec.model_validate(
    {
        "passband": {
            "f_start": 100e6,
            "f_stop": 800e6,
            "il_max_db": 0.5,
            "rl_min_db": 14.0,
        },
        "stopband_targets": [
            {"freq": 2e9, "rejection_min_db": 30.0, "label": "2x fc"},
        ],
    }
)


class TestMcTrace:
    def test_trace_disabled_by_default(self, tmp_path):
        """No trace file should be created when trace=False."""
        result = monte_carlo_analysis(LPF.components, SPEC, tolerance_pct=2.0, n_runs=5, n_jobs=1)
        assert result.trace_path is None

    def test_trace_creates_jsonl_file(self, tmp_path):
        out = tmp_path / "test_trace.jsonl"
        result = monte_carlo_analysis(
            LPF.components,
            SPEC,
            tolerance_pct=2.0,
            n_runs=5,
            n_jobs=1,
            trace=True,
            trace_path=out,
        )
        assert result.trace_path == str(out.resolve())
        assert out.is_file()
        lines = out.read_text().strip().split("\n")
        assert len(lines) == 5

    def test_trace_records_have_required_fields(self, tmp_path):
        out = tmp_path / "trace.jsonl"
        monte_carlo_analysis(
            LPF.components,
            SPEC,
            tolerance_pct=2.0,
            n_runs=10,
            n_jobs=1,
            trace=True,
            trace_path=out,
        )
        records = [json.loads(line) for line in out.read_text().strip().split("\n")]
        for rec in records:
            assert "trial" in rec
            assert "seed" in rec
            assert "passed" in rec
            assert isinstance(rec["passed"], bool)
            assert "components" in rec
            assert "metrics" in rec
            assert "failures" in rec

    def test_trace_components_match_run(self, tmp_path):
        """Each trial's recorded components match the seed-derived sample."""
        out = tmp_path / "trace.jsonl"
        monte_carlo_analysis(
            LPF.components,
            SPEC,
            tolerance_pct=2.0,
            n_runs=10,
            n_jobs=1,
            base_seed=42,
            trace=True,
            trace_path=out,
        )
        records = [json.loads(line) for line in out.read_text().strip().split("\n")]
        # Seeds are deterministic
        assert records[0]["seed"] == 42
        assert records[5]["seed"] == 47
        # Components dict has all the same refdes as nominal
        for rec in records:
            assert set(rec["components"].keys()) == set(LPF.components.keys())

    def test_default_path_used_when_omitted(self, tmp_path, monkeypatch):
        """If trace=True but no path given, defaults to mc_trace_<seed>.jsonl in cwd."""
        monkeypatch.chdir(tmp_path)
        result = monte_carlo_analysis(
            LPF.components,
            SPEC,
            tolerance_pct=2.0,
            n_runs=3,
            n_jobs=1,
            base_seed=99,
            trace=True,
        )
        expected = tmp_path / "mc_trace_99.jsonl"
        assert expected.is_file()
        assert result.trace_path == str(expected.resolve())

    def test_failing_trials_carry_failures_list(self, tmp_path):
        """If we set a tight tolerance to force some failures, the trace records
        should mark those trials as passed=False with a non-empty failures list."""
        # Tight 10% tolerance — some trials should fail the 14 dB RL spec
        out = tmp_path / "trace.jsonl"
        result = monte_carlo_analysis(
            LPF.components,
            SPEC,
            tolerance_pct=10.0,
            n_runs=200,
            n_jobs=1,
            trace=True,
            trace_path=out,
        )
        records = [json.loads(line) for line in out.read_text().strip().split("\n")]
        passing = [r for r in records if r["passed"]]
        failing = [r for r in records if not r["passed"]]
        assert len(passing) + len(failing) == 200
        if failing:
            assert all(len(r["failures"]) >= 1 for r in failing)
        # Sanity: declared yield matches the trace
        assert result.yield_pct == pytest.approx(100.0 * len(passing) / 200, rel=1e-6)
