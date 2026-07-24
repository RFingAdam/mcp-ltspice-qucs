"""Central resource budgets for analytical design tools."""

from __future__ import annotations

MAX_COMPONENTS = 128
MAX_CONCURRENCY = 8
MAX_FREQUENCY_POINTS = 10_000
MAX_MONTE_CARLO_RUNS = 10_000
MAX_OPTIMIZER_ITERATIONS = 5_000
MAX_SWEEP_POINTS = 10_000
MAX_INLINE_SWEEP_POINTS = 1_000
MAX_ANALYTICAL_WORK_UNITS = 25_000_000


def require_work_budget(*, evaluations: int, frequency_points: int, label: str) -> int:
    """Validate and return the estimated scalar frequency evaluations."""
    work_units = evaluations * frequency_points
    if work_units > MAX_ANALYTICAL_WORK_UNITS:
        raise ValueError(
            f"{label} estimated cost is {work_units:,} frequency evaluations, "
            f"exceeding the safe per-call limit of {MAX_ANALYTICAL_WORK_UNITS:,}; "
            "reduce trials/iterations/points or submit a separately approved job"
        )
    return work_units
