"""Monte Carlo yield analysis with Gaussian-distributed component tolerances."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from joblib import Parallel, delayed

from mcp_ltspice.eval import FilterSpec
from mcp_ltspice.extract import (
    components_dict_to_elements,
    ladder_sparams_from_components,
)


@dataclass
class MonteCarloResult:
    n_runs: int
    n_passing: int
    yield_pct: float
    per_metric_stats: dict[str, dict[str, float]]
    failing_criteria_counts: dict[str, int]
    trace_path: str | None = None  # set when trace=True; JSONL file with per-trial records


def _single_run(
    seed: int,
    components: dict[str, float],
    tolerance_pct: dict[str, float] | float,
    spec: FilterSpec,
    transmission_zeros: bool | None,
    f_grid: np.ndarray,
    z0: float,
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    sampled = {}
    for refdes, nominal in components.items():
        tol = tolerance_pct[refdes] if isinstance(tolerance_pct, dict) else tolerance_pct
        # Gaussian: ±3σ ≈ tolerance window
        sigma = nominal * (tol / 100.0) / 3.0
        sampled[refdes] = max(rng.normal(nominal, sigma), nominal * 0.01)

    elements = components_dict_to_elements(
        sampled, transmission_zeros=transmission_zeros, topology="series_first"
    )
    s = ladder_sparams_from_components(elements, f_grid, z0=z0)
    s21_db = 20 * np.log10(np.maximum(np.abs(s[:, 1, 0]), 1e-12))
    s11_db = 20 * np.log10(np.maximum(np.abs(s[:, 0, 0]), 1e-12))

    metrics: dict[str, float] = {}
    failures: list[str] = []

    pb = spec.passband
    pb_mask = (f_grid >= pb.f_start) & (f_grid <= pb.f_stop)
    if pb_mask.any():
        worst_il = float(-s21_db[pb_mask].min())
        worst_rl = float(-s11_db[pb_mask].max())
        metrics["passband_il_db"] = worst_il
        metrics["passband_rl_db"] = worst_rl
        if worst_il > pb.il_max_db:
            failures.append("Passband IL")
        if worst_rl < pb.rl_min_db:
            failures.append("Passband RL")

    for tgt in spec.stopband_targets:
        s21_at = float(np.interp(tgt.freq, f_grid, s21_db))
        rejection = -s21_at
        metrics[f"rejection@{tgt.label}"] = rejection
        if rejection < tgt.rejection_min_db:
            failures.append(tgt.label)

    return {
        "passed": len(failures) == 0,
        "failures": failures,
        "metrics": metrics,
        "components": sampled,
    }


def monte_carlo_analysis(
    components: dict[str, float],
    spec: FilterSpec | dict,
    *,
    tolerance_pct: dict[str, float] | float = 5.0,
    n_runs: int = 1000,
    z0: float = 50.0,
    transmission_zeros: bool | None = None,
    f_grid_npoints: int = 401,
    n_jobs: int = -1,
    base_seed: int = 0,
    trace: bool = False,
    trace_path: str | Path | None = None,
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

    pb = spec.passband
    span = max(
        pb.f_stop * 5, max((t.freq for t in spec.stopband_targets), default=pb.f_stop * 5) * 1.5
    )
    f_grid = np.geomspace(max(pb.f_start, 1e3), span, f_grid_npoints)

    results = Parallel(n_jobs=n_jobs)(
        delayed(_single_run)(
            base_seed + i,
            components,
            tolerance_pct,
            spec,
            transmission_zeros,
            f_grid,
            z0,
        )
        for i in range(n_runs)
    )

    n_pass = sum(1 for r in results if r["passed"])
    fail_counts: dict[str, int] = {}
    for r in results:
        for f in r["failures"]:
            fail_counts[f] = fail_counts.get(f, 0) + 1

    # Per-metric stats
    metric_keys = list(results[0]["metrics"].keys())
    stats: dict[str, dict[str, float]] = {}
    for k in metric_keys:
        values = np.asarray([r["metrics"][k] for r in results])
        stats[k] = {
            "mean": float(np.mean(values)),
            "std": float(np.std(values)),
            "p05": float(np.percentile(values, 5)),
            "p50": float(np.percentile(values, 50)),
            "p95": float(np.percentile(values, 95)),
        }

    trace_path_str: str | None = None
    if trace:
        if trace_path is None:
            trace_path = Path(f"mc_trace_{base_seed}.jsonl")
        trace_path = Path(trace_path)
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        with trace_path.open("w", encoding="utf-8") as fh:
            for i, r in enumerate(results):
                rec = {
                    "trial": i,
                    "seed": base_seed + i,
                    "passed": r["passed"],
                    "failures": r["failures"],
                    "components": r["components"],
                    "metrics": r["metrics"],
                }
                fh.write(json.dumps(rec) + "\n")
        trace_path_str = str(trace_path.resolve())

    return MonteCarloResult(
        n_runs=n_runs,
        n_passing=n_pass,
        yield_pct=100.0 * n_pass / n_runs,
        per_metric_stats=stats,
        failing_criteria_counts=fail_counts,
        trace_path=trace_path_str,
    )
