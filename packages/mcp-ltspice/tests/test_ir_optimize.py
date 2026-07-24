from __future__ import annotations

import numpy as np
import pytest

from mcp_ltspice.ir_optimize import (
    SpiceDatasetEvaluator,
    TraceMetric,
    _apply_spice_environment,
    evaluate_trace_metric,
)
from rf_mcp_common.backend import ResultAxis, ResultDataset, trace_from_array
from rf_mcp_common.circuit_ir import (
    CircuitAnalysis,
    CircuitComponent,
    CircuitDirective,
    CircuitDocument,
    CircuitNode,
)
from rf_mcp_common.optimization import DesignCorner


def _dataset() -> ResultDataset:
    values = np.asarray([1 + 0j, 0.5 + 0.5j, 0 + 1j])
    return ResultDataset(
        backend="ngspice",
        analysis="ac",
        axis=ResultAxis(name="frequency", unit="Hz", values=[1e6, 2e6, 3e6]),
        traces={
            "V(out)": trace_from_array(
                "V(out)",
                values,
                unit="V",
                quantity="voltage",
            )
        },
        method="fixture",
    )


def test_trace_metric_interpolates_exact_requested_axis_value() -> None:
    metric = TraceMetric(
        name="gain",
        trace="V(out)",
        projection="magnitude",
        reduction="at",
        axis_value=1.5e6,
    )
    assert evaluate_trace_metric(_dataset(), metric) == pytest.approx((1.0 + np.sqrt(0.5)) / 2)


def test_trace_metric_reductions_respect_axis_band() -> None:
    metric = TraceMetric(
        name="worst",
        trace="V(out)",
        projection="magnitude_db",
        reduction="min",
        axis_min=1.5e6,
        axis_max=2.5e6,
    )
    assert evaluate_trace_metric(_dataset(), metric) == pytest.approx(20 * np.log10(np.sqrt(0.5)))


def test_trace_metric_rejects_extrapolation() -> None:
    metric = TraceMetric(
        name="outside",
        trace="V(out)",
        reduction="at",
        axis_value=10e6,
    )
    with pytest.raises(ValueError, match="outside the selected range"):
        evaluate_trace_metric(_dataset(), metric)


def test_phase_interpolation_unwraps_across_180_degrees() -> None:
    dataset = ResultDataset(
        backend="ngspice",
        analysis="ac",
        axis=ResultAxis(name="frequency", unit="Hz", values=[1.0, 2.0]),
        traces={
            "V(out)": trace_from_array(
                "V(out)",
                np.exp(1j * np.deg2rad([179.0, -179.0])),
                unit="V",
                quantity="voltage",
            )
        },
        method="phase-wrap-fixture",
    )
    metric = TraceMetric(
        name="phase",
        trace="V(out)",
        projection="phase_deg",
        reduction="at",
        axis_value=1.5,
    )
    assert evaluate_trace_metric(dataset, metric) == pytest.approx(180.0)


def test_spice_environment_is_explicitly_applied_or_rejected() -> None:
    document = CircuitDocument(
        document_id="corner-fixture",
        source_format="generated",
        nodes=[CircuitNode(id="0", is_ground=True)],
        components=[],
        directives=[
            CircuitDirective(text=".temp 25"),
            CircuitDirective(text=".param VDD=3.3"),
        ],
    )
    hot = _apply_spice_environment(
        document,
        DesignCorner(
            name="hot",
            environment={"temperature_c": 125.0, "param.VDD": 3.0},
        ),
    )
    assert [item.text for item in hot.directives] == [".temp 125", ".param VDD=3"]
    assert [item.text for item in document.directives] == [".temp 25", ".param VDD=3.3"]

    with pytest.raises(ValueError, match="unsupported SPICE corner"):
        _apply_spice_environment(
            document,
            DesignCorner(name="bad", environment={"supply_v": 3.3}),
        )


@pytest.mark.ngspice
@pytest.mark.integration
def test_spice_dataset_evaluator_runs_real_ac_points_per_decade(tmp_path) -> None:
    document = CircuitDocument(
        document_id="optimizer-divider",
        source_format="generated",
        nodes=[
            CircuitNode(id="0", is_ground=True),
            CircuitNode(id="in"),
            CircuitNode(id="out"),
        ],
        components=[
            CircuitComponent(
                refdes="V1",
                kind="voltage_source",
                pins={"1": "in", "2": "0"},
                parameters={"source_expression": "AC 1"},
            ),
            CircuitComponent(
                refdes="R1",
                kind="resistor",
                pins={"1": "in", "2": "out"},
                value="1k",
            ),
            CircuitComponent(
                refdes="R2",
                kind="resistor",
                pins={"1": "out", "2": "0"},
                value="1k",
            ),
        ],
    )
    evaluator = SpiceDatasetEvaluator(
        backend="ngspice",
        analysis=CircuitAnalysis(
            id="ac1",
            kind="ac",
            parameters={
                "sweep": "dec",
                "points": 10,
                "f_start_hz": 1e3,
                "f_stop_hz": 1e6,
            },
        ),
        metrics=[
            TraceMetric(
                name="gain",
                trace="V(out)",
                reduction="at",
                axis_value=10e3,
            )
        ],
        workspace_root=tmp_path,
        sandbox=False,
    )
    result = evaluator(document, DesignCorner(name="nominal"))
    assert result.metrics["gain"] == pytest.approx(0.5, rel=1e-5)
