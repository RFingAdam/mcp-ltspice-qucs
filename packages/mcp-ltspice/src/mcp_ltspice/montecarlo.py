"""Monte Carlo yield analysis with Gaussian-distributed component tolerances."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from joblib import Parallel, delayed

from mcp_ltspice.analysis_context import (
    EvaluationMode,
    FilterAnalysisContext,
    FilterKind,
    build_spec_frequency_grid,
    evaluate_component_margins,
)
from mcp_ltspice.eval import FilterSpec
from mcp_ltspice.resource_budget import (
    MAX_COMPONENTS,
    MAX_CONCURRENCY,
    MAX_FREQUENCY_POINTS,
    MAX_MONTE_CARLO_RUNS,
    require_work_budget,
)
from mcp_ltspice.synthesis import Topology
from rf_mcp_common.simulation_workspace import ProcessCancelledError, SimulationWorkspace


@dataclass
class MonteCarloResult:
    n_runs: int
    n_passing: int
    yield_pct: float
    per_metric_stats: dict[str, dict[str, float]]
    failing_criteria_counts: dict[str, int]
    analysis_context: dict[str, Any]
    estimated_work_units: int
    effective_n_jobs: int
    trace_path: str | None = None  # set when trace=True; JSONL file with per-trial records
    trace_manifest: str | None = None


def _single_run(
    seed: int,
    components: dict[str, float],
    tolerance_pct: dict[str, float] | float,
    spec: FilterSpec,
    context: FilterAnalysisContext,
    f_grid: np.ndarray,
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    sampled = {}
    for refdes, nominal in components.items():
        tol = tolerance_pct[refdes] if isinstance(tolerance_pct, dict) else tolerance_pct
        # Gaussian: ±3σ ≈ tolerance window
        sigma = nominal * (tol / 100.0) / 3.0
        sampled[refdes] = max(rng.normal(nominal, sigma), nominal * 0.01)

    evaluation = evaluate_component_margins(
        sampled,
        spec,
        context=context,
        f_grid=f_grid,
    )
    metrics: dict[str, float] = {}
    failures: list[str] = []

    metrics["passband_il_db"] = evaluation.measured["Passband IL"]
    metrics["passband_rl_db"] = evaluation.measured["Passband RL"]
    for target in spec.stopband_targets:
        metrics[f"rejection@{target.label}"] = evaluation.measured[target.label]
    failures.extend(label for label, margin in evaluation.margins.items() if margin < 0)

    return {
        "passed": len(failures) == 0,
        "failures": failures,
        "metrics": metrics,
        "components": sampled,
    }


def monte_carlo_analysis(
    components: dict[str, float],
    spec: FilterSpec | dict[str, Any],
    *,
    tolerance_pct: dict[str, float] | float = 5.0,
    n_runs: int = 1000,
    z0: float = 50.0,
    transmission_zeros: bool | None = None,
    kind: FilterKind | str = "lowpass",
    topology: Topology | str = Topology.SERIES_FIRST,
    evaluation_mode: EvaluationMode = "analytical",
    model_fidelity: str = "ideal_lumped",
    provenance: dict[str, Any] | None = None,
    component_substitution: dict[str, dict[str, Any]] | None = None,
    transmission_zeros_hz: list[float] | None = None,
    f_grid_npoints: int = 401,
    n_jobs: int = 1,
    base_seed: int = 0,
    trace: bool = False,
    trace_path: str | Path | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> MonteCarloResult:
    """Sample components with Gaussian tolerance and report yield + per-metric stats.

    - ``tolerance_pct``: scalar (applied to every component) or dict per refdes.
      A 5% tolerance means ±5% at 3σ.
    - ``n_runs``: number of Monte Carlo trials.
    - ``n_jobs``: joblib parallelism. -1 = all cores.
    - ``trace``: if ``True``, emit a JSONL file with one record per trial
      (``{seed, components, metrics, passed, failures}``) so the engineer
      can drill into the failing 1−yield% to identify which components
      dominate yield loss. Useful for debugging tight specs and for
      sensitivity-after-the-fact analysis.
    - ``trace_path``: where to write the trace. Defaults to
      ``./mc_trace_<base_seed>.jsonl`` if ``trace=True`` and no path is given.
    """
    if isinstance(spec, dict):
        spec = FilterSpec.model_validate(spec)
    if n_runs <= 0:
        raise ValueError(f"n_runs must be > 0, got {n_runs}")
    if not components or len(components) > MAX_COMPONENTS:
        raise ValueError(f"components must contain between 1 and {MAX_COMPONENTS} entries")
    if n_runs > MAX_MONTE_CARLO_RUNS:
        raise ValueError("n_runs exceeds the safe per-call limit of 10,000")
    if n_jobs == 0 or n_jobs < -1 or n_jobs > MAX_CONCURRENCY:
        raise ValueError("n_jobs must be -1 or an integer in [1, 8]")
    if not 8 <= f_grid_npoints <= MAX_FREQUENCY_POINTS:
        raise ValueError("f_grid_npoints exceeds the safe limit of 10,000")
    estimated_work_units = require_work_budget(
        evaluations=n_runs,
        frequency_points=f_grid_npoints,
        label="Monte Carlo",
    )
    effective_n_jobs = min(os.cpu_count() or 1, MAX_CONCURRENCY) if n_jobs == -1 else n_jobs
    context = FilterAnalysisContext.create(
        kind=kind,
        topology=topology,
        z0=z0,
        transmission_zeros=transmission_zeros,
        evaluation_mode=evaluation_mode,
        model_fidelity=model_fidelity,
        provenance=provenance,
        component_substitution=component_substitution,
    )

    f_grid = build_spec_frequency_grid(
        spec,
        npoints=f_grid_npoints,
        transmission_zeros_hz=transmission_zeros_hz or (),
    )

    trace_path_str: str | None = None
    trace_target: Path | None = None
    trace_staging: Path | None = None
    trace_handle = None
    trace_workspace: SimulationWorkspace | None = None
    if trace:
        estimated_trace_bytes = n_runs * (
            256 + 64 * len(components) + 64 * (2 + len(spec.stopband_targets))
        )
        if estimated_trace_bytes > 64 * 1024 * 1024:
            raise ValueError(
                f"estimated Monte Carlo trace size {estimated_trace_bytes:,} bytes "
                "exceeds the 64 MiB artifact limit"
            )
        if trace_path is None:
            trace_workspace = SimulationWorkspace.create("monte-carlo")
            trace_target = trace_workspace.output_path(f"mc_trace_{base_seed}.jsonl")
        else:
            trace_target = Path(trace_path).resolve()
        trace_target.parent.mkdir(parents=True, exist_ok=True)
        trace_staging = trace_target.with_name(f".{trace_target.name}.{base_seed}.tmp")
        trace_handle = trace_staging.open("w", encoding="utf-8")

    n_pass = 0
    fail_counts: dict[str, int] = {}
    metric_values: dict[str, list[float]] = {}
    try:
        batch_size = min(256, n_runs)
        for batch_start in range(0, n_runs, batch_size):
            if cancel_requested is not None and cancel_requested():
                raise ProcessCancelledError("Monte Carlo analysis was cancelled")
            batch_stop = min(batch_start + batch_size, n_runs)
            batch = Parallel(n_jobs=effective_n_jobs)(
                delayed(_single_run)(
                    base_seed + i,
                    components,
                    tolerance_pct,
                    spec,
                    context,
                    f_grid,
                )
                for i in range(batch_start, batch_stop)
            )
            for i, result in zip(range(batch_start, batch_stop), batch, strict=True):
                n_pass += int(result["passed"])
                for failure in result["failures"]:
                    fail_counts[failure] = fail_counts.get(failure, 0) + 1
                for key, value in result["metrics"].items():
                    metric_values.setdefault(key, []).append(float(value))
                if trace_handle is not None:
                    trace_handle.write(
                        json.dumps(
                            {
                                "trial": i,
                                "seed": base_seed + i,
                                "passed": result["passed"],
                                "failures": result["failures"],
                                "components": result["components"],
                                "metrics": result["metrics"],
                                "analysis_context": context.as_dict(),
                            }
                        )
                        + "\n"
                    )
            if progress is not None:
                progress(batch_stop, n_runs)
        if trace_handle is not None and trace_target is not None and trace_staging is not None:
            trace_handle.close()
            trace_handle = None
            os.replace(trace_staging, trace_target)
            trace_path_str = str(trace_target)
            if trace_workspace is not None:
                trace_workspace.record_artifact(trace_target, role="monte_carlo_trace")
                trace_workspace.complete(returncode=0)
    finally:
        if trace_handle is not None:
            trace_handle.close()
        if trace_staging is not None and trace_staging.exists():
            trace_staging.unlink()

    stats: dict[str, dict[str, float]] = {}
    for key, samples in metric_values.items():
        values = np.asarray(samples)
        stats[key] = {
            "mean": float(np.mean(values)),
            "std": float(np.std(values)),
            "p05": float(np.percentile(values, 5)),
            "p50": float(np.percentile(values, 50)),
            "p95": float(np.percentile(values, 95)),
        }

    return MonteCarloResult(
        n_runs=n_runs,
        n_passing=n_pass,
        yield_pct=100.0 * n_pass / n_runs,
        per_metric_stats=stats,
        failing_criteria_counts=fail_counts,
        analysis_context=context.as_dict(),
        estimated_work_units=estimated_work_units,
        effective_n_jobs=effective_n_jobs,
        trace_path=trace_path_str,
        trace_manifest=(
            str(trace_workspace.manifest_path) if trace_workspace is not None else None
        ),
    )
