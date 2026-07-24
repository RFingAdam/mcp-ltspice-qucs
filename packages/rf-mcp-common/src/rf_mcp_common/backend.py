"""Simulator adapter contract and cross-backend result normalization."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rf_mcp_common.circuit_ir import CircuitAnalysis, CircuitDocument

BackendName = Literal["ngspice", "ltspice", "qucsator", "xyce"]
AnalysisKind = Literal["op", "dc", "ac", "transient", "sparameters", "noise", "harmonic_balance"]


class BackendCapability(BaseModel):
    """Readiness and supported-analysis contract for one backend."""

    model_config = ConfigDict(extra="forbid")

    backend: BackendName
    installed: bool
    launchable: bool
    validated: bool
    version: str | None = None
    supported_analyses: list[AnalysisKind]
    diagnostic: str | None = None
    sandbox_profile: dict[str, Any] = Field(default_factory=dict)

    def require(self, analysis: AnalysisKind) -> None:
        if not self.launchable:
            raise RuntimeError(
                f"{self.backend} is not launchable"
                + (f": {self.diagnostic}" if self.diagnostic else "")
            )
        if analysis not in self.supported_analyses:
            raise ValueError(f"{self.backend} does not support {analysis}")


class BackendArtifact(BaseModel):
    """Compiled simulator input linked to the source IR."""

    model_config = ConfigDict(extra="forbid")

    backend: BackendName
    filename: str
    media_type: str
    content: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    circuit_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    analysis: CircuitAnalysis
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_text(
        cls,
        *,
        backend: BackendName,
        filename: str,
        media_type: str,
        content: str,
        document: CircuitDocument,
        analysis: CircuitAnalysis,
        metadata: dict[str, Any] | None = None,
    ) -> BackendArtifact:
        return cls(
            backend=backend,
            filename=filename,
            media_type=media_type,
            content=content,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            circuit_fingerprint=document.electrical_fingerprint(),
            analysis=analysis,
            metadata=metadata or {},
        )


class BackendRunRequest(BaseModel):
    """Bounded request passed from a durable job to an adapter."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    artifact: BackendArtifact
    workspace: Path
    timeout_sec: float = Field(default=120.0, gt=0, le=86_400)
    sandbox: bool = True


class RawBackendResult(BaseModel):
    """Opaque simulator output captured before normalization."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    backend: BackendName
    analysis: AnalysisKind
    artifact_paths: list[Path]
    stdout: str = ""
    stderr: str = ""
    returncode: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResultAxis(BaseModel):
    """Independent variable shared by every result trace."""

    model_config = ConfigDict(extra="forbid")

    name: str
    unit: str
    values: list[float]

    @model_validator(mode="after")
    def _validate_axis(self) -> ResultAxis:
        values = np.asarray(self.values, dtype=float)
        if values.ndim != 1 or values.size == 0:
            raise ValueError("result axis must be a non-empty vector")
        if not np.all(np.isfinite(values)):
            raise ValueError("result axis contains non-finite values")
        if values.size > 1 and np.any(np.diff(values) <= 0):
            raise ValueError("result axis must be strictly increasing")
        return self


class ResultTrace(BaseModel):
    """Real or complex normalized trace."""

    model_config = ConfigDict(extra="forbid")

    name: str
    unit: str
    quantity: str
    real: list[float]
    imag: list[float] | None = None

    def complex_array(self) -> np.ndarray:
        real = np.asarray(self.real, dtype=float)
        if self.imag is None:
            return real.astype(np.complex128)
        return real + 1j * np.asarray(self.imag, dtype=float)


class ResultDataset(BaseModel):
    """Backend-neutral result dataset."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    backend: BackendName
    backend_version: str | None = None
    analysis: AnalysisKind
    axis: ResultAxis
    traces: dict[str, ResultTrace]
    method: str
    assumptions: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_traces(self) -> ResultDataset:
        size = len(self.axis.values)
        for key, trace in self.traces.items():
            if key != trace.name:
                raise ValueError(f"trace key {key!r} does not match trace name {trace.name!r}")
            if len(trace.real) != size or (trace.imag is not None and len(trace.imag) != size):
                raise ValueError(f"trace {key!r} does not match axis length {size}")
            values = trace.complex_array()
            if not np.all(np.isfinite(values.real)) or not np.all(np.isfinite(values.imag)):
                raise ValueError(f"trace {key!r} contains non-finite values")
        return self


class TolerancePolicy(BaseModel):
    """Documented comparison thresholds for one analysis family."""

    model_config = ConfigDict(extra="forbid")

    policy_id: str
    analysis: AnalysisKind
    complex_abs: float | None = Field(default=None, ge=0)
    relative: float | None = Field(default=None, ge=0)
    magnitude_db: float | None = Field(default=None, ge=0)
    phase_deg: float | None = Field(default=None, ge=0)
    absolute_floor: float = Field(default=1e-12, gt=0)
    rationale: str


DEFAULT_TOLERANCE_POLICIES: dict[AnalysisKind, TolerancePolicy] = {
    "op": TolerancePolicy(
        policy_id="op-v1",
        analysis="op",
        relative=1e-4,
        complex_abs=1e-9,
        rationale="DC operating points should agree to solver tolerances.",
    ),
    "dc": TolerancePolicy(
        policy_id="dc-v1",
        analysis="dc",
        relative=1e-3,
        complex_abs=1e-8,
        rationale="DC sweeps allow small device-model and solver differences.",
    ),
    "ac": TolerancePolicy(
        policy_id="ac-v1",
        analysis="ac",
        magnitude_db=0.10,
        phase_deg=1.0,
        complex_abs=1e-3,
        rationale="Linear AC results are compared in magnitude, circular phase, and complex value.",
    ),
    "sparameters": TolerancePolicy(
        policy_id="sparameters-v1",
        analysis="sparameters",
        magnitude_db=0.10,
        phase_deg=1.0,
        complex_abs=1e-2,
        rationale="S-parameter comparison includes complex residual and RF presentation metrics.",
    ),
    "transient": TolerancePolicy(
        policy_id="transient-v1",
        analysis="transient",
        relative=2e-3,
        complex_abs=1e-8,
        rationale="Transient integration methods can differ slightly after interpolation.",
    ),
    "noise": TolerancePolicy(
        policy_id="noise-v1",
        analysis="noise",
        magnitude_db=0.20,
        relative=1e-2,
        rationale="Noise model implementations vary more than ideal linear analyses.",
    ),
    "harmonic_balance": TolerancePolicy(
        policy_id="harmonic-balance-v1",
        analysis="harmonic_balance",
        magnitude_db=0.50,
        phase_deg=3.0,
        relative=5e-2,
        rationale="Nonlinear steady-state solvers use different convergence algorithms.",
    ),
}


class TraceComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace: str
    points: int
    complex_abs_max: float
    relative_max: float
    magnitude_db_max: float
    phase_deg_max: float
    passed: bool
    failed_metrics: list[str] = Field(default_factory=list)


class DatasetComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left_backend: BackendName
    right_backend: BackendName
    analysis: AnalysisKind
    policy: TolerancePolicy
    overlap: tuple[float, float]
    traces: list[TraceComparison]
    passed: bool


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    backend: BackendName
    analysis: AnalysisKind
    checks: list[dict[str, Any]]
    warnings: list[str] = Field(default_factory=list)


def normalize_dataset(dataset: ResultDataset) -> ResultDataset:
    """Return a deterministic dataset with canonical axis units and trace order."""
    unit_scale = {
        ("frequency", "Hz"): 1.0,
        ("frequency", "kHz"): 1e3,
        ("frequency", "MHz"): 1e6,
        ("frequency", "GHz"): 1e9,
        ("time", "s"): 1.0,
        ("time", "ms"): 1e-3,
        ("time", "us"): 1e-6,
        ("time", "ns"): 1e-9,
        ("index", "1"): 1.0,
        ("sweep", "1"): 1.0,
        ("sweep", "V"): 1.0,
        ("sweep", "A"): 1.0,
    }
    key = (dataset.axis.name, dataset.axis.unit)
    scale = unit_scale.get(key)
    if scale is None:
        raise ValueError(f"no normalization policy for axis {key!r}")
    canonical_unit = (
        "Hz"
        if dataset.axis.name == "frequency"
        else "s"
        if dataset.axis.name == "time"
        else dataset.axis.unit
    )
    axis = ResultAxis(
        name=dataset.axis.name,
        unit=canonical_unit,
        values=(np.asarray(dataset.axis.values, dtype=float) * scale).tolist(),
    )
    return dataset.model_copy(
        update={"axis": axis, "traces": dict(sorted(dataset.traces.items()))},
        deep=True,
    )


def _interpolate_complex(x: np.ndarray, y: np.ndarray, target: np.ndarray) -> np.ndarray:
    return np.asarray(
        np.interp(target, x, y.real) + 1j * np.interp(target, x, y.imag),
        dtype=np.complex128,
    )


def compare_datasets(
    left: ResultDataset,
    right: ResultDataset,
    *,
    policy: TolerancePolicy | None = None,
    trace_map: Mapping[str, str] | None = None,
) -> DatasetComparison:
    """Compare equivalent analyses on the union grid within their overlap."""
    left = normalize_dataset(left)
    right = normalize_dataset(right)
    if left.analysis != right.analysis:
        raise ValueError(f"analysis mismatch: {left.analysis} versus {right.analysis}")
    if left.axis.name != right.axis.name or left.axis.unit != right.axis.unit:
        raise ValueError("normalized result axes are incompatible")
    selected_policy = policy or DEFAULT_TOLERANCE_POLICIES[left.analysis]
    if selected_policy.analysis != left.analysis:
        raise ValueError("tolerance policy analysis does not match datasets")
    left_x = np.asarray(left.axis.values)
    right_x = np.asarray(right.axis.values)
    low = max(float(left_x[0]), float(right_x[0]))
    high = min(float(left_x[-1]), float(right_x[-1]))
    if high < low:
        raise ValueError("datasets have no overlapping axis interval")
    target = np.unique(
        np.concatenate(
            [
                left_x[(left_x >= low) & (left_x <= high)],
                right_x[(right_x >= low) & (right_x <= high)],
            ]
        )
    )
    if target.size == 0:
        raise ValueError("datasets have no comparable points")
    mapping = dict(trace_map or {name: name for name in left.traces if name in right.traces})
    if not mapping:
        raise ValueError("datasets have no mapped traces")
    comparisons: list[TraceComparison] = []
    floor = selected_policy.absolute_floor
    for left_name, right_name in mapping.items():
        if left_name not in left.traces or right_name not in right.traces:
            raise ValueError(f"missing mapped trace {left_name!r} -> {right_name!r}")
        left_values = _interpolate_complex(left_x, left.traces[left_name].complex_array(), target)
        right_values = _interpolate_complex(
            right_x, right.traces[right_name].complex_array(), target
        )
        residual = np.abs(left_values - right_values)
        denominator = np.maximum(np.maximum(np.abs(left_values), np.abs(right_values)), floor)
        relative = residual / denominator
        left_db = 20.0 * np.log10(np.maximum(np.abs(left_values), floor))
        right_db = 20.0 * np.log10(np.maximum(np.abs(right_values), floor))
        phase_delta = np.angle(
            np.exp(1j * (np.angle(left_values) - np.angle(right_values))),
            deg=True,
        )
        metrics = {
            "complex_abs": float(np.max(residual)),
            "relative": float(np.max(relative)),
            "magnitude_db": float(np.max(np.abs(left_db - right_db))),
            "phase_deg": float(np.max(np.abs(phase_delta))),
        }
        failed = [
            name
            for name, threshold in {
                "complex_abs": selected_policy.complex_abs,
                "relative": selected_policy.relative,
                "magnitude_db": selected_policy.magnitude_db,
                "phase_deg": selected_policy.phase_deg,
            }.items()
            if threshold is not None and metrics[name] > threshold
        ]
        comparisons.append(
            TraceComparison(
                trace=left_name,
                points=int(target.size),
                complex_abs_max=metrics["complex_abs"],
                relative_max=metrics["relative"],
                magnitude_db_max=metrics["magnitude_db"],
                phase_deg_max=metrics["phase_deg"],
                passed=not failed,
                failed_metrics=failed,
            )
        )
    return DatasetComparison(
        left_backend=left.backend,
        right_backend=right.backend,
        analysis=left.analysis,
        policy=selected_policy,
        overlap=(low, high),
        traces=comparisons,
        passed=all(item.passed for item in comparisons),
    )


def validate_dataset(dataset: ResultDataset, analysis: CircuitAnalysis) -> ValidationReport:
    """Apply backend-neutral structural and request-consistency checks."""
    checks: list[dict[str, Any]] = []
    checks.append({"name": "analysis_kind", "passed": dataset.analysis == analysis.kind})
    checks.append({"name": "axis_nonempty", "passed": bool(dataset.axis.values)})
    checks.append({"name": "traces_nonempty", "passed": bool(dataset.traces)})
    expected_points = analysis.parameters.get("points")
    sweep = str(analysis.parameters.get("sweep", "")).lower()
    semantics = analysis.parameters.get("points_semantics")
    if semantics is None:
        semantics = (
            "per_decade"
            if analysis.kind == "ac" and sweep == "dec"
            else "per_octave"
            if analysis.kind == "ac" and sweep == "oct"
            else "total"
        )
    expected_total: int | None = None
    if isinstance(expected_points, int) and semantics == "total":
        expected_total = expected_points
    elif isinstance(expected_points, int) and semantics in {"per_decade", "per_octave"}:
        start = analysis.parameters.get("f_start_hz")
        stop = analysis.parameters.get("f_stop_hz")
        if isinstance(start, (int, float)) and isinstance(stop, (int, float)) and stop > start > 0:
            span = np.log10(stop / start) if semantics == "per_decade" else np.log2(stop / start)
            expected_total = round(float(span) * expected_points) + 1
    if expected_total is not None:
        checks.append(
            {
                "name": "point_count",
                "passed": len(dataset.axis.values) == expected_total,
                "expected": expected_total,
                "actual": len(dataset.axis.values),
                "semantics": semantics,
            }
        )
    if analysis.kind in {"ac", "noise", "sparameters"}:
        start = analysis.parameters.get("f_start_hz")
        stop = analysis.parameters.get("f_stop_hz")
        if isinstance(start, (int, float)) and isinstance(stop, (int, float)):
            actual_start = dataset.axis.values[0]
            actual_stop = dataset.axis.values[-1]
            checks.append(
                {
                    "name": "frequency_coverage",
                    "passed": bool(
                        np.isclose(actual_start, start, rtol=1e-9, atol=0)
                        and np.isclose(actual_stop, stop, rtol=1e-9, atol=0)
                    ),
                    "expected": [float(start), float(stop)],
                    "actual": [actual_start, actual_stop],
                }
            )
    return ValidationReport(
        valid=all(bool(check["passed"]) for check in checks),
        backend=dataset.backend,
        analysis=dataset.analysis,
        checks=checks,
    )


@runtime_checkable
class BackendAdapter(Protocol):
    """Common contract implemented by every simulator adapter."""

    backend: BackendName

    def probe(self, *, validate: bool = False) -> BackendCapability: ...

    def import_file(self, path: str | Path) -> CircuitDocument: ...

    def compile(self, document: CircuitDocument, analysis: CircuitAnalysis) -> BackendArtifact: ...

    def run(self, request: BackendRunRequest) -> RawBackendResult: ...

    def parse(self, raw: RawBackendResult) -> ResultDataset: ...

    def validate(self, dataset: ResultDataset, analysis: CircuitAnalysis) -> ValidationReport: ...


class BackendAdapterBase(ABC):
    """Base class providing capability negotiation and result validation."""

    backend: BackendName

    @abstractmethod
    def probe(self, *, validate: bool = False) -> BackendCapability:
        raise NotImplementedError

    @abstractmethod
    def import_file(self, path: str | Path) -> CircuitDocument:
        raise NotImplementedError

    @abstractmethod
    def compile(self, document: CircuitDocument, analysis: CircuitAnalysis) -> BackendArtifact:
        raise NotImplementedError

    @abstractmethod
    def run(self, request: BackendRunRequest) -> RawBackendResult:
        raise NotImplementedError

    @abstractmethod
    def parse(self, raw: RawBackendResult) -> ResultDataset:
        raise NotImplementedError

    def validate(self, dataset: ResultDataset, analysis: CircuitAnalysis) -> ValidationReport:
        return validate_dataset(dataset, analysis)

    def require_analysis(self, analysis: CircuitAnalysis) -> None:
        self.probe(validate=False).require(analysis.kind)


def trace_from_array(
    name: str,
    values: np.ndarray,
    *,
    unit: str,
    quantity: str,
) -> ResultTrace:
    """Build a JSON-safe trace from a real or complex NumPy array."""
    array = np.asarray(values)
    if array.ndim != 1:
        raise ValueError(f"trace {name!r} must be one-dimensional")
    complex_values = array.astype(np.complex128)
    imag = complex_values.imag.tolist() if np.any(complex_values.imag) else None
    return ResultTrace(
        name=name,
        unit=unit,
        quantity=quantity,
        real=complex_values.real.tolist(),
        imag=imag,
    )


__all__ = [
    "DEFAULT_TOLERANCE_POLICIES",
    "AnalysisKind",
    "BackendAdapter",
    "BackendAdapterBase",
    "BackendArtifact",
    "BackendCapability",
    "BackendName",
    "BackendRunRequest",
    "DatasetComparison",
    "RawBackendResult",
    "ResultAxis",
    "ResultDataset",
    "ResultTrace",
    "TolerancePolicy",
    "TraceComparison",
    "ValidationReport",
    "compare_datasets",
    "normalize_dataset",
    "trace_from_array",
    "validate_dataset",
]
