"""Generic, topology-preserving optimization over ``CircuitDocument`` values.

The engine is deliberately simulator-agnostic.  Evaluators return named
metrics plus the exact model hashes they instantiated.  This makes analytical
screening cheap while allowing a separate simulator evaluator to prove that the
final candidate and yield corners used the selected component models.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import Literal, Protocol

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rf_mcp_common.backend import BackendName
from rf_mcp_common.circuit_ir import CircuitChange, CircuitDocument

EvaluationBackend = BackendName | Literal["analytical"]


class OptimizationVariable(BaseModel):
    """One numeric component value/parameter without topology mutation."""

    model_config = ConfigDict(extra="forbid")

    path: str
    lower: float | None = None
    upper: float | None = None
    choices: list[float] | None = None
    initial: float | None = None
    scale: Literal["linear", "log"] = "linear"
    tolerance_pct: float = Field(default=0.0, ge=0, le=100)

    @model_validator(mode="after")
    def _validate_bounds(self) -> OptimizationVariable:
        if not re.fullmatch(r"components\.[^.]+\.(?:value|parameters\.[^.]+)", self.path):
            raise ValueError(
                "variable path must be components.<refdes>.value or "
                "components.<refdes>.parameters.<name>"
            )
        bounded = self.lower is not None or self.upper is not None
        if bool(self.choices) == bounded:
            raise ValueError("provide either non-empty choices or lower/upper bounds")
        if bounded:
            if self.lower is None or self.upper is None:
                raise ValueError("both lower and upper are required")
            if self.lower >= self.upper:
                raise ValueError("lower must be less than upper")
            if self.scale == "log" and self.lower <= 0:
                raise ValueError("log-scaled lower bound must be positive")
        elif self.choices is not None:
            if not self.choices:
                raise ValueError("choices cannot be empty")
            if not all(math.isfinite(value) for value in self.choices):
                raise ValueError("choices must be finite")
        return self


class MetricObjective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    goal: Literal["minimize", "maximize", "target"]
    target: float | None = None
    weight: float = Field(default=1.0, gt=0)
    scale: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def _target_required(self) -> MetricObjective:
        if self.goal == "target" and self.target is None:
            raise ValueError("target objective requires target")
        return self


class MetricConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    operator: Literal["le", "ge", "between"]
    limit: float | None = None
    lower: float | None = None
    upper: float | None = None

    @model_validator(mode="after")
    def _validate_limit(self) -> MetricConstraint:
        if self.operator in {"le", "ge"} and self.limit is None:
            raise ValueError(f"{self.operator} constraint requires limit")
        if self.operator == "between" and (
            self.lower is None or self.upper is None or self.lower > self.upper
        ):
            raise ValueError("between constraint requires lower <= upper")
        return self


class DesignCorner(BaseModel):
    """Environmental/model corner supplied to an evaluator."""

    model_config = ConfigDict(extra="forbid")

    name: str
    component_multipliers: dict[str, float] = Field(default_factory=dict)
    environment: dict[str, float | str | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _multipliers_positive(self) -> DesignCorner:
        if any(
            not math.isfinite(value) or value <= 0 for value in self.component_multipliers.values()
        ):
            raise ValueError("component corner multipliers must be positive and finite")
        return self


class EvaluationResult(BaseModel):
    """One evaluator response with model-use attestation."""

    model_config = ConfigDict(extra="forbid")

    metrics: dict[str, float]
    backend: EvaluationBackend
    method: str
    model_hashes_used: dict[str, str] = Field(default_factory=dict)
    provenance: dict[str, str | float | int | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _finite_metrics(self) -> EvaluationResult:
        if not self.metrics:
            raise ValueError("evaluation returned no metrics")
        if any(not math.isfinite(value) for value in self.metrics.values()):
            raise ValueError("evaluation metrics must be finite")
        return self


class CandidateEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    values: dict[str, float]
    score: float
    feasible: bool
    constraint_violation: float
    metrics_by_corner: dict[str, dict[str, float]]
    backend_by_corner: dict[str, EvaluationBackend]


class YieldEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    samples: int
    passed: int
    yield_fraction: float
    seed: int
    backend: EvaluationBackend
    used_selected_models: bool


class DesignChangeReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["screened", "simulator_validated", "constraints_failed"]
    algorithm: str
    seed: int
    candidates_evaluated: int
    original_fingerprint: str
    final_fingerprint: str
    changes: list[CircuitChange]
    model_hashes: dict[str, str]
    screening_backend: EvaluationBackend
    validation_backend: EvaluationBackend | None = None
    objectives: list[MetricObjective]
    constraints: list[MetricConstraint]
    corner_metrics: dict[str, dict[str, float]]
    constraints_passed: bool
    constraint_violation: float
    independent_validation: bool
    yield_estimate: YieldEstimate | None = None


class OptimizationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_document: CircuitDocument
    best_values: dict[str, float]
    screening_trace: list[CandidateEvaluation]
    validation_results: dict[str, EvaluationResult]
    report: DesignChangeReport


class OptimizationProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: CircuitDocument
    variables: list[OptimizationVariable]
    objectives: list[MetricObjective]
    constraints: list[MetricConstraint] = Field(default_factory=list)
    corners: list[DesignCorner] = Field(default_factory=lambda: [DesignCorner(name="nominal")])
    iterations: int = Field(default=128, ge=1, le=10_000)
    seed: int = 0
    yield_samples: int = Field(default=0, ge=0, le=10_000)
    require_model_validation: bool = False
    require_independent_backend: bool = True

    @model_validator(mode="after")
    def _validate_problem(self) -> OptimizationProblem:
        if not self.variables:
            raise ValueError("optimization requires at least one variable")
        if not self.objectives:
            raise ValueError("optimization requires at least one objective")
        paths = [variable.path for variable in self.variables]
        if len(paths) != len(set(paths)):
            raise ValueError("optimization variable paths must be unique")
        corner_names = [corner.name for corner in self.corners]
        if len(corner_names) != len(set(corner_names)):
            raise ValueError("corner names must be unique")
        components = {component.refdes: component for component in self.document.components}
        for variable in self.variables:
            parts = variable.path.split(".")
            component = components.get(parts[1])
            if component is None:
                raise ValueError(f"unknown component in variable path {variable.path!r}")
            if (
                parts[2] == "value"
                and component.model is not None
                and component.model.model_kind != "lumped_approximation"
            ):
                raise ValueError(
                    f"{variable.path}: cannot vary the nominal value of a fixed "
                    f"{component.model.model_kind} model; optimize an explicit "
                    "instance parameter or select another model"
                )
        for corner in self.corners:
            for refdes in corner.component_multipliers:
                component = components.get(refdes)
                if component is None:
                    raise ValueError(
                        f"corner {corner.name!r} references unknown component {refdes!r}"
                    )
                if (
                    component.model is not None
                    and component.model.model_kind != "lumped_approximation"
                ):
                    raise ValueError(
                        f"corner {corner.name!r} cannot scale the nominal value of "
                        f"fixed model on {refdes}; use an explicit model parameter "
                        "or model variant"
                    )
        return self


class CircuitEvaluator(Protocol):
    def __call__(self, document: CircuitDocument, corner: DesignCorner) -> EvaluationResult: ...


def _engineering_float(value: str | float | int | bool | None) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"value {value!r} is not numeric")
    if isinstance(value, (float, int)):
        result = float(value)
    else:
        match = re.fullmatch(
            r"\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*([A-Za-zµ]*)\s*",
            value,
        )
        if match is None:
            raise ValueError(f"value {value!r} is not an engineering number")
        suffix = match.group(2).lower()
        scales = {
            "": 1.0,
            "f": 1e-15,
            "p": 1e-12,
            "n": 1e-9,
            "u": 1e-6,
            "µ": 1e-6,
            "m": 1e-3,
            "k": 1e3,
            "meg": 1e6,
            "g": 1e9,
            "t": 1e12,
        }
        if suffix not in scales:
            raise ValueError(f"unknown engineering suffix {suffix!r}")
        result = float(match.group(1)) * scales[suffix]
    if not math.isfinite(result):
        raise ValueError("numeric value must be finite")
    return result


def _current_value(document: CircuitDocument, path: str) -> float:
    parts = path.split(".")
    component = next(
        (item for item in document.components if item.refdes == parts[1]),
        None,
    )
    if component is None:
        raise ValueError(f"unknown component in variable path {path!r}")
    value = component.value if parts[2] == "value" else component.parameters.get(parts[3])
    return _engineering_float(value)


def _apply_values(
    document: CircuitDocument,
    values: dict[str, float],
    *,
    operation: str,
) -> CircuitDocument:
    changes = [
        CircuitChange(
            path=path,
            before=(
                next(
                    item for item in document.components if item.refdes == path.split(".")[1]
                ).value
                if path.split(".")[2] == "value"
                else next(
                    item for item in document.components if item.refdes == path.split(".")[1]
                ).parameters.get(path.split(".")[3])
            ),
            after=value,
            reason=operation,
        )
        for path, value in values.items()
    ]
    return document.transformed(changes, operation=operation)


def _corner_document(document: CircuitDocument, corner: DesignCorner) -> CircuitDocument:
    changes: list[CircuitChange] = []
    by_ref = {component.refdes: component for component in document.components}
    for refdes, multiplier in corner.component_multipliers.items():
        component = by_ref.get(refdes)
        if component is None:
            raise ValueError(f"corner {corner.name!r} references unknown component {refdes!r}")
        before = component.value
        changes.append(
            CircuitChange(
                path=f"components.{refdes}.value",
                before=before,
                after=_engineering_float(before) * multiplier,
                reason=f"corner:{corner.name}",
            )
        )
    return (
        document.transformed(changes, operation=f"apply-corner:{corner.name}")
        if changes
        else document
    )


def _model_hashes(document: CircuitDocument) -> dict[str, str]:
    return {
        component.refdes: component.model.checksum_sha256
        for component in document.components
        if component.model is not None
    }


def _evaluate(
    document: CircuitDocument,
    corners: list[DesignCorner],
    evaluator: CircuitEvaluator,
    *,
    required_metrics: set[str],
    require_models: bool,
) -> dict[str, EvaluationResult]:
    expected_hashes = _model_hashes(document)
    if require_models and not expected_hashes:
        raise ValueError("model-aware validation requested but no component models are selected")
    results: dict[str, EvaluationResult] = {}
    for corner in corners:
        result = evaluator(_corner_document(document, corner), corner)
        missing = sorted(required_metrics - set(result.metrics))
        if missing:
            raise ValueError(
                f"evaluator omitted metrics at corner {corner.name}: {', '.join(missing)}"
            )
        if require_models and result.model_hashes_used != expected_hashes:
            raise ValueError(
                f"evaluator model attestation mismatch at corner {corner.name}: "
                f"expected {expected_hashes}, got {result.model_hashes_used}"
            )
        results[corner.name] = result
    return results


def _constraint_violation(
    results: dict[str, EvaluationResult], constraints: list[MetricConstraint]
) -> tuple[bool, float]:
    total = 0.0
    for constraint in constraints:
        values = [result.metrics[constraint.metric] for result in results.values()]
        for value in values:
            if constraint.operator == "le":
                assert constraint.limit is not None
                total += max(0.0, value - constraint.limit) / max(abs(constraint.limit), 1e-12)
            elif constraint.operator == "ge":
                assert constraint.limit is not None
                total += max(0.0, constraint.limit - value) / max(abs(constraint.limit), 1e-12)
            else:
                assert constraint.lower is not None
                assert constraint.upper is not None
                width = max(constraint.upper - constraint.lower, 1e-12)
                total += max(0.0, constraint.lower - value, value - constraint.upper) / width
    return total <= 1e-15, total


def _objective_score(
    results: dict[str, EvaluationResult], objectives: list[MetricObjective]
) -> float:
    score = 0.0
    for objective in objectives:
        values = [result.metrics[objective.metric] for result in results.values()]
        if objective.goal == "minimize":
            term = max(values) / objective.scale
        elif objective.goal == "maximize":
            term = -min(values) / objective.scale
        else:
            assert objective.target is not None
            term = max(abs(value - objective.target) for value in values) / objective.scale
        score += objective.weight * term
    return score


def _sample_candidates(
    problem: OptimizationProblem, rng: np.random.Generator
) -> list[dict[str, float]]:
    initial: dict[str, float] = {}
    for variable in problem.variables:
        value = (
            variable.initial
            if variable.initial is not None
            else _current_value(problem.document, variable.path)
        )
        if variable.choices is not None and value not in variable.choices:
            raise ValueError(f"initial value for {variable.path} is not one of its choices")
        if variable.lower is not None and value < variable.lower:
            raise ValueError(f"initial value for {variable.path} is below its lower bound")
        if variable.upper is not None and value > variable.upper:
            raise ValueError(f"initial value for {variable.path} is above its upper bound")
        initial[variable.path] = value
    candidates = [initial]
    while len(candidates) < problem.iterations:
        candidate: dict[str, float] = {}
        for variable in problem.variables:
            if variable.choices:
                candidate[variable.path] = float(rng.choice(variable.choices))
            else:
                assert variable.lower is not None
                assert variable.upper is not None
                unit = float(rng.random())
                candidate[variable.path] = (
                    math.exp(
                        math.log(variable.lower)
                        + unit * (math.log(variable.upper) - math.log(variable.lower))
                    )
                    if variable.scale == "log"
                    else variable.lower + unit * (variable.upper - variable.lower)
                )
        candidates.append(candidate)
    return candidates


def _yield_estimate(
    problem: OptimizationProblem,
    final_document: CircuitDocument,
    best_values: dict[str, float],
    evaluator: CircuitEvaluator,
    *,
    required_metrics: set[str],
    seed: int,
    cancel_requested: Callable[[], bool] | None,
    progress: Callable[[int, int], None] | None,
    progress_offset: int,
    progress_total: int,
) -> YieldEstimate | None:
    if problem.yield_samples == 0:
        return None
    rng = np.random.default_rng(seed)
    passed = 0
    backend: EvaluationBackend | None = None
    for sample_index in range(problem.yield_samples):
        if cancel_requested is not None and cancel_requested():
            raise RuntimeError("optimization cancelled")
        sample: dict[str, float] = {}
        for variable in problem.variables:
            nominal = best_values[variable.path]
            if variable.tolerance_pct <= 0 or variable.choices:
                value = nominal
            else:
                sigma = variable.tolerance_pct / 300.0
                value = nominal * (1.0 + float(rng.normal(0.0, sigma)))
                if variable.lower is not None:
                    value = max(value, variable.lower)
                if variable.upper is not None:
                    value = min(value, variable.upper)
            sample[variable.path] = value
        sampled = _apply_values(final_document, sample, operation="yield-sample")
        results = _evaluate(
            sampled,
            problem.corners,
            evaluator,
            required_metrics=required_metrics,
            require_models=problem.require_model_validation,
        )
        backend = next(iter(results.values())).backend
        feasible, _ = _constraint_violation(results, problem.constraints)
        passed += int(feasible)
        if progress is not None:
            progress(progress_offset + sample_index + 1, progress_total)
    assert backend is not None
    return YieldEstimate(
        samples=problem.yield_samples,
        passed=passed,
        yield_fraction=passed / problem.yield_samples,
        seed=seed,
        backend=backend,
        used_selected_models=problem.require_model_validation,
    )


def optimize_circuit(
    problem: OptimizationProblem,
    evaluator: CircuitEvaluator,
    *,
    validation_evaluator: CircuitEvaluator | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> OptimizationResult:
    """Run deterministic bounded/discrete screening and final validation."""
    problem.document.require_supported()
    if problem.require_independent_backend and validation_evaluator is None:
        raise ValueError(
            "independent validation requested but no validation evaluator was supplied"
        )
    rng = np.random.default_rng(problem.seed)
    required_metrics = {
        *[objective.metric for objective in problem.objectives],
        *[constraint.metric for constraint in problem.constraints],
    }
    trace: list[CandidateEvaluation] = []
    best_document: CircuitDocument | None = None
    best_values: dict[str, float] | None = None
    best_key = (True, math.inf, math.inf)
    screening_backend: EvaluationBackend | None = None
    progress_total = problem.iterations + problem.yield_samples + 1

    for index, values in enumerate(_sample_candidates(problem, rng)):
        if cancel_requested is not None and cancel_requested():
            raise RuntimeError("optimization cancelled")
        candidate = _apply_values(problem.document, values, operation="optimization-candidate")
        results = _evaluate(
            candidate,
            problem.corners,
            evaluator,
            required_metrics=required_metrics,
            require_models=False,
        )
        backends = {result.backend for result in results.values()}
        if len(backends) != 1:
            raise ValueError("one screening evaluator returned different backends by corner")
        screening_backend = next(iter(backends))
        feasible, violation = _constraint_violation(results, problem.constraints)
        objective_score = _objective_score(results, problem.objectives)
        score = objective_score + 1e6 * violation
        trace.append(
            CandidateEvaluation(
                index=index,
                values=values,
                score=score,
                feasible=feasible,
                constraint_violation=violation,
                metrics_by_corner={name: dict(result.metrics) for name, result in results.items()},
                backend_by_corner={name: result.backend for name, result in results.items()},
            )
        )
        # Feasibility is a hard ordering, not an arbitrary penalty weight.
        # Among infeasible candidates, minimize violation before objectives.
        selection_key = (not feasible, violation, objective_score)
        if selection_key < best_key:
            best_key = selection_key
            best_document = candidate
            best_values = values
        if progress is not None:
            progress(index + 1, progress_total)

    assert best_document is not None
    assert best_values is not None
    assert screening_backend is not None
    final_evaluator = validation_evaluator or evaluator
    validation = _evaluate(
        best_document,
        problem.corners,
        final_evaluator,
        required_metrics=required_metrics,
        require_models=problem.require_model_validation,
    )
    if progress is not None:
        progress(problem.iterations + 1, progress_total)
    validation_backends = {result.backend for result in validation.values()}
    if len(validation_backends) != 1:
        raise ValueError("validation evaluator returned different backends by corner")
    validation_backend = next(iter(validation_backends))
    independent = validation_backend != screening_backend
    if problem.require_independent_backend and validation_evaluator is not None and not independent:
        raise ValueError(
            "independent validation requested but screening and validation used "
            f"{validation_backend}"
        )
    constraints_passed, constraint_violation = _constraint_violation(
        validation, problem.constraints
    )
    yield_seed = problem.seed ^ 0x5EED5EED
    yield_result = _yield_estimate(
        problem,
        best_document,
        best_values,
        final_evaluator,
        required_metrics=required_metrics,
        seed=yield_seed,
        cancel_requested=cancel_requested,
        progress=progress,
        progress_offset=problem.iterations + 1,
        progress_total=progress_total,
    )
    changes = [
        CircuitChange(
            path=path,
            before=_current_value(problem.document, path),
            after=value,
            reason="optimized",
        )
        for path, value in best_values.items()
        if not math.isclose(
            _current_value(problem.document, path),
            value,
            rel_tol=1e-15,
            abs_tol=0.0,
        )
    ]
    simulator_validated = (
        validation_backend != "analytical"
        and (not problem.require_model_validation or bool(_model_hashes(best_document)))
        and (independent or not problem.require_independent_backend)
    )
    status: Literal["screened", "simulator_validated", "constraints_failed"]
    if not constraints_passed:
        status = "constraints_failed"
    elif simulator_validated:
        status = "simulator_validated"
    else:
        status = "screened"
    report = DesignChangeReport(
        status=status,
        algorithm="seeded_random_bounded_v1",
        seed=problem.seed,
        candidates_evaluated=len(trace),
        original_fingerprint=problem.document.electrical_fingerprint(),
        final_fingerprint=best_document.electrical_fingerprint(),
        changes=changes,
        model_hashes=_model_hashes(best_document),
        screening_backend=screening_backend,
        validation_backend=validation_backend,
        objectives=problem.objectives,
        constraints=problem.constraints,
        corner_metrics={name: dict(result.metrics) for name, result in validation.items()},
        constraints_passed=constraints_passed,
        constraint_violation=constraint_violation,
        independent_validation=independent,
        yield_estimate=yield_result,
    )
    return OptimizationResult(
        final_document=best_document,
        best_values=best_values,
        screening_trace=trace,
        validation_results=validation,
        report=report,
    )


def render_design_change_report(result: OptimizationResult) -> str:
    """Render the machine-readable report as concise Markdown."""
    report = result.report
    lines = [
        f"# Circuit optimization report: {result.final_document.document_id}",
        "",
        f"- Status: `{report.status}`",
        f"- Algorithm: `{report.algorithm}`",
        f"- Seed: `{report.seed}`",
        f"- Candidates evaluated: `{report.candidates_evaluated}`",
        f"- Screening backend: `{report.screening_backend}`",
        f"- Validation backend: `{report.validation_backend}`",
        f"- Independent validation: `{report.independent_validation}`",
        f"- Constraints passed: `{report.constraints_passed}`",
        f"- Constraint violation: `{report.constraint_violation:.12g}`",
        "",
        "## Objectives",
        "",
    ]
    lines.extend(
        f"- `{objective.metric}`: `{objective.goal}`"
        + (f" target `{objective.target}`" if objective.target is not None else "")
        + f", weight `{objective.weight}`, scale `{objective.scale}`"
        for objective in report.objectives
    )
    lines.extend(["", "## Constraints", ""])
    if report.constraints:
        lines.extend(
            f"- `{constraint.metric}`: `{constraint.operator}` "
            + (
                f"`{constraint.limit}`"
                if constraint.limit is not None
                else f"`[{constraint.lower}, {constraint.upper}]`"
            )
            for constraint in report.constraints
        )
    else:
        lines.append("- No constraints.")
    lines.extend(["", "## Validation metrics by corner", ""])
    lines.extend(
        f"- `{corner}`: "
        + ", ".join(f"`{metric}={value:.12g}`" for metric, value in sorted(metrics.items()))
        for corner, metrics in sorted(report.corner_metrics.items())
    )
    lines.extend(
        [
            "",
            "## Design changes",
            "",
        ]
    )
    if report.changes:
        lines.extend(
            f"- `{change.path}`: `{change.before}` → `{change.after}`" for change in report.changes
        )
    else:
        lines.append("- No value changes.")
    lines.extend(["", "## Selected model hashes", ""])
    if report.model_hashes:
        lines.extend(
            f"- `{refdes}`: `{checksum}`"
            for refdes, checksum in sorted(report.model_hashes.items())
        )
    else:
        lines.append("- No component models selected.")
    if report.yield_estimate is not None:
        estimate = report.yield_estimate
        lines.extend(
            [
                "",
                "## Yield",
                "",
                f"- Passed: `{estimate.passed}/{estimate.samples}`",
                f"- Estimated yield: `{estimate.yield_fraction:.6f}`",
                f"- Seed: `{estimate.seed}`",
                f"- Backend: `{estimate.backend}`",
            ]
        )
    return "\n".join(lines) + "\n"


__all__ = [
    "CandidateEvaluation",
    "CircuitEvaluator",
    "DesignChangeReport",
    "DesignCorner",
    "EvaluationResult",
    "MetricConstraint",
    "MetricObjective",
    "OptimizationProblem",
    "OptimizationResult",
    "OptimizationVariable",
    "YieldEstimate",
    "optimize_circuit",
    "render_design_change_report",
]
