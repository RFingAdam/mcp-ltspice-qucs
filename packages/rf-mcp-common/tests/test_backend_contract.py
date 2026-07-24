from __future__ import annotations

import numpy as np
import pytest

from rf_mcp_common.backend import (
    DEFAULT_TOLERANCE_POLICIES,
    ResultAxis,
    ResultDataset,
    compare_datasets,
    normalize_dataset,
    trace_from_array,
    validate_dataset,
)
from rf_mcp_common.circuit_ir import CircuitAnalysis


def _dataset(
    backend: str,
    frequency: list[float],
    values: list[complex],
    *,
    unit: str = "Hz",
) -> ResultDataset:
    return ResultDataset(
        backend=backend,  # type: ignore[arg-type]
        analysis="sparameters",
        axis=ResultAxis(name="frequency", unit=unit, values=frequency),
        traces={
            "S[2,1]": trace_from_array(
                "S[2,1]",
                np.asarray(values, dtype=np.complex128),
                unit="1",
                quantity="sparameter",
            )
        },
        method="fixture",
    )


def test_normalization_converts_axis_units() -> None:
    dataset = _dataset("ngspice", [1.0, 2.0], [1 + 0j, 0.5 + 0j], unit="GHz")
    normalized = normalize_dataset(dataset)
    assert normalized.axis.unit == "Hz"
    assert normalized.axis.values == [1e9, 2e9]


def test_cross_backend_comparison_uses_union_overlap_grid() -> None:
    left = _dataset(
        "ngspice",
        [1.0, 2.0, 3.0],
        [1 + 0j, 0.75 + 0j, 0.5 + 0j],
    )
    right = _dataset(
        "qucsator",
        [1.0, 1.5, 2.5, 3.0],
        [1 + 0j, 0.875 + 0j, 0.625 + 0j, 0.5 + 0j],
    )
    comparison = compare_datasets(left, right)
    assert comparison.traces[0].points == 5
    assert comparison.passed
    assert comparison.policy.policy_id == "sparameters-v1"


def test_cross_backend_comparison_detects_interior_difference() -> None:
    left = _dataset("ngspice", [1.0, 2.0, 3.0], [1 + 0j, 1 + 0j, 1 + 0j])
    right = _dataset(
        "qucsator",
        [1.0, 1.5, 2.5, 3.0],
        [1 + 0j, 0.1 + 0j, 0.1 + 0j, 1 + 0j],
    )
    comparison = compare_datasets(left, right)
    assert not comparison.passed
    assert comparison.traces[0].magnitude_db_max > 10


def test_phase_comparison_is_circular() -> None:
    left = _dataset("ngspice", [1.0], [np.exp(1j * np.deg2rad(179))])
    right = _dataset("qucsator", [1.0], [np.exp(1j * np.deg2rad(-179))])
    comparison = compare_datasets(left, right)
    assert comparison.traces[0].phase_deg_max == pytest.approx(2.0)
    # The default 1-degree threshold still correctly fails this fixture.
    assert "phase_deg" in comparison.traces[0].failed_metrics


def test_every_analysis_has_documented_tolerance_policy() -> None:
    assert set(DEFAULT_TOLERANCE_POLICIES) == {
        "op",
        "dc",
        "ac",
        "transient",
        "sparameters",
        "noise",
        "harmonic_balance",
    }
    assert all(policy.rationale for policy in DEFAULT_TOLERANCE_POLICIES.values())


def test_ac_point_validation_understands_points_per_decade() -> None:
    dataset = ResultDataset(
        backend="ngspice",
        analysis="ac",
        axis=ResultAxis(
            name="frequency",
            unit="Hz",
            values=np.geomspace(1e3, 1e6, 31).tolist(),
        ),
        traces={
            "V(out)": trace_from_array(
                "V(out)",
                np.ones(31),
                unit="V",
                quantity="voltage",
            )
        },
        method="fixture",
    )
    analysis = CircuitAnalysis(
        id="ac1",
        kind="ac",
        parameters={
            "sweep": "dec",
            "points": 10,
            "f_start_hz": 1e3,
            "f_stop_hz": 1e6,
        },
    )
    report = validate_dataset(dataset, analysis)
    assert report.valid
    point_check = next(check for check in report.checks if check["name"] == "point_count")
    assert point_check["expected"] == 31
