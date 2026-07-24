"""Simulator-backed metric evaluator for generic CircuitDocument optimization."""

from __future__ import annotations

import math
import re
import threading
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, model_validator

from mcp_ltspice.backend_adapters import LTspiceAdapter, NgspiceAdapter, SpiceRawAdapter
from rf_mcp_common.backend import (
    BackendRunRequest,
    ResultDataset,
)
from rf_mcp_common.circuit_ir import CircuitAnalysis, CircuitDirective, CircuitDocument
from rf_mcp_common.optimization import DesignCorner, EvaluationResult


class TraceMetric(BaseModel):
    """A named scalar derived from one normalized simulator trace."""

    model_config = ConfigDict(extra="forbid")

    name: str
    trace: str
    projection: Literal["real", "imag", "magnitude", "magnitude_db", "phase_deg"] = "magnitude"
    reduction: Literal["at", "min", "max", "mean", "rms"] = "at"
    axis_value: float | None = None
    axis_min: float | None = None
    axis_max: float | None = None

    @model_validator(mode="after")
    def _validate_at(self) -> TraceMetric:
        if self.reduction == "at" and self.axis_value is None:
            raise ValueError("reduction='at' requires axis_value")
        if (
            self.axis_min is not None
            and self.axis_max is not None
            and self.axis_min > self.axis_max
        ):
            raise ValueError("axis_min cannot exceed axis_max")
        return self


def _projection(values: np.ndarray, kind: str) -> np.ndarray:
    if kind == "real":
        return values.real
    if kind == "imag":
        return values.imag
    if kind == "magnitude":
        return np.asarray(np.abs(values), dtype=float)
    if kind == "magnitude_db":
        return np.asarray(
            20.0 * np.log10(np.maximum(np.abs(values), 1e-15)),
            dtype=float,
        )
    if kind == "phase_deg":
        return np.asarray(np.rad2deg(np.unwrap(np.angle(values))), dtype=float)
    raise ValueError(f"unknown projection {kind!r}")


def _apply_spice_environment(
    document: CircuitDocument,
    corner: DesignCorner,
) -> CircuitDocument:
    """Translate supported corner settings into explicit SPICE directives."""
    directives = list(document.directives)
    for key, value in corner.environment.items():
        if key == "temperature_c":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("temperature_c must be numeric")
            directives = [item for item in directives if not item.text.lower().startswith(".temp")]
            directives.append(CircuitDirective(text=f".temp {float(value):.12g}", dialect="spice"))
            continue
        if key.startswith("param."):
            name = key.removeprefix("param.")
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
                raise ValueError(f"invalid SPICE corner parameter name {name!r}")
            if isinstance(value, bool):
                rendered = "1" if value else "0"
            elif isinstance(value, (int, float)) and math.isfinite(float(value)):
                rendered = f"{float(value):.12g}"
            else:
                raise ValueError(f"SPICE corner parameter {name!r} must be finite numeric/bool")
            prefix = f".param {name.lower()}="
            directives = [item for item in directives if not item.text.lower().startswith(prefix)]
            directives.append(CircuitDirective(text=f".param {name}={rendered}", dialect="spice"))
            continue
        raise ValueError(
            f"unsupported SPICE corner environment key {key!r}; use temperature_c or param.<name>"
        )
    return document.model_copy(update={"directives": directives}, deep=True)


def evaluate_trace_metric(dataset: ResultDataset, metric: TraceMetric) -> float:
    trace_name = metric.trace
    if trace_name not in dataset.traces:
        matches = [name for name in dataset.traces if name.casefold() == trace_name.casefold()]
        if len(matches) == 1:
            trace_name = matches[0]
        else:
            raise ValueError(
                f"trace {metric.trace!r} is unavailable or ambiguous; "
                f"found {sorted(dataset.traces)}"
            )
    axis = np.asarray(dataset.axis.values, dtype=float)
    projected = _projection(dataset.traces[trace_name].complex_array(), metric.projection)
    mask = np.ones(axis.shape, dtype=bool)
    if metric.axis_min is not None:
        mask &= axis >= metric.axis_min
    if metric.axis_max is not None:
        mask &= axis <= metric.axis_max
    if not np.any(mask):
        raise ValueError(f"metric {metric.name!r} selects no axis points")
    selected_axis = axis[mask]
    selected = projected[mask]
    if metric.reduction == "at":
        assert metric.axis_value is not None
        if metric.axis_value < selected_axis[0] or metric.axis_value > selected_axis[-1]:
            raise ValueError(f"metric {metric.name!r} axis_value is outside the selected range")
        return float(np.interp(metric.axis_value, selected_axis, selected))
    if metric.reduction == "min":
        return float(np.min(selected))
    if metric.reduction == "max":
        return float(np.max(selected))
    if metric.reduction == "mean":
        return float(np.mean(selected))
    return float(math.sqrt(float(np.mean(np.square(selected)))))


class SpiceDatasetEvaluator:
    """Compile, run, parse, and scalarize one circuit per optimizer call."""

    def __init__(
        self,
        *,
        backend: Literal["ngspice", "ltspice"],
        analysis: CircuitAnalysis,
        metrics: list[TraceMetric],
        workspace_root: str | Path,
        timeout_sec: float = 120.0,
        sandbox: bool = True,
    ):
        if not metrics:
            raise ValueError("at least one trace metric is required")
        names = [metric.name for metric in metrics]
        if len(names) != len(set(names)):
            raise ValueError("trace metric names must be unique")
        self.adapter: SpiceRawAdapter = (
            NgspiceAdapter() if backend == "ngspice" else LTspiceAdapter()
        )
        self.analysis = analysis
        self.metrics = metrics
        self.workspace_root = Path(workspace_root)
        self.timeout_sec = timeout_sec
        self.sandbox = sandbox
        self._counter = 0
        self._lock = threading.Lock()

    def __call__(self, document: CircuitDocument, corner: DesignCorner) -> EvaluationResult:
        with self._lock:
            index = self._counter
            self._counter += 1
        workspace = self.workspace_root / f"evaluation-{index:06d}-{corner.name}"
        corner_document = _apply_spice_environment(document, corner)
        artifact = self.adapter.compile(corner_document, self.analysis)
        raw = self.adapter.run(
            BackendRunRequest(
                artifact=artifact,
                workspace=workspace,
                timeout_sec=self.timeout_sec,
                sandbox=self.sandbox,
            )
        )
        dataset = self.adapter.parse(raw)
        validation = self.adapter.validate(dataset, self.analysis)
        if not validation.valid:
            raise ValueError(f"backend result failed validation: {validation.model_dump()}")
        return EvaluationResult(
            metrics={
                metric.name: evaluate_trace_metric(dataset, metric) for metric in self.metrics
            },
            backend=self.adapter.backend,
            method="compiled_circuit_ir_simulation",
            model_hashes_used=dict(artifact.metadata.get("model_hashes") or {}),
            provenance={
                "input_sha256": artifact.content_sha256,
                "circuit_fingerprint": artifact.circuit_fingerprint,
                "evaluation_index": index,
            },
        )


__all__ = [
    "SpiceDatasetEvaluator",
    "TraceMetric",
    "_apply_spice_environment",
    "evaluate_trace_metric",
]
