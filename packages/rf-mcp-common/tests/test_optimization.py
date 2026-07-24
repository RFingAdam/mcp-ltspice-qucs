from __future__ import annotations

import pytest

from rf_mcp_common.circuit_ir import (
    CircuitComponent,
    CircuitDocument,
    CircuitNode,
    ModelReference,
)
from rf_mcp_common.optimization import (
    DesignCorner,
    EvaluationResult,
    MetricConstraint,
    MetricObjective,
    OptimizationProblem,
    OptimizationVariable,
    optimize_circuit,
    render_design_change_report,
)


def _document() -> CircuitDocument:
    return CircuitDocument(
        document_id="optimizer-fixture",
        source_format="generated",
        nodes=[
            CircuitNode(id="0", is_ground=True),
            CircuitNode(id="in"),
            CircuitNode(id="out"),
        ],
        components=[
            CircuitComponent(
                refdes="R1",
                kind="resistor",
                pins={"1": "in", "2": "out"},
                value=1.0,
                model=ModelReference(
                    provider="fixture",
                    checksum_sha256="a" * 64,
                    source_reference="fixture://R1",
                    model_kind="lumped_approximation",
                    pin_map={"positive": 1, "negative": 2},
                    manufacturer_part_number="R-ONE",
                ),
            ),
            CircuitComponent(
                refdes="R2",
                kind="resistor",
                pins={"1": "out", "2": "0"},
                value=2.0,
                model=ModelReference(
                    provider="fixture",
                    checksum_sha256="b" * 64,
                    source_reference="fixture://R2",
                    model_kind="lumped_approximation",
                    pin_map={"positive": 1, "negative": 2},
                    manufacturer_part_number="R-TWO",
                ),
            ),
        ],
    )


def _value(document: CircuitDocument, refdes: str) -> float:
    return float(next(item for item in document.components if item.refdes == refdes).value)


def _model_hashes(document: CircuitDocument) -> dict[str, str]:
    return {
        component.refdes: component.model.checksum_sha256
        for component in document.components
        if component.model
    }


def _screen(document: CircuitDocument, corner: DesignCorner) -> EvaluationResult:
    loss = (_value(document, "R1") - 3.0) ** 2
    return EvaluationResult(
        metrics={"loss": loss, "headroom": 10.0 - loss},
        backend="analytical",
        method=f"fixture-screen:{corner.name}",
    )


def _validate(document: CircuitDocument, corner: DesignCorner) -> EvaluationResult:
    loss = (_value(document, "R1") - 3.0) ** 2
    return EvaluationResult(
        metrics={"loss": loss, "headroom": 10.0 - loss},
        backend="ngspice",
        method=f"fixture-simulator:{corner.name}",
        model_hashes_used=_model_hashes(document),
        provenance={"model_mode": "instantiated"},
    )


def _problem() -> OptimizationProblem:
    return OptimizationProblem(
        document=_document(),
        variables=[
            OptimizationVariable(
                path="components.R1.value",
                choices=[1.0, 3.0, 5.0],
                initial=1.0,
                tolerance_pct=0,
            )
        ],
        objectives=[MetricObjective(metric="loss", goal="minimize")],
        constraints=[MetricConstraint(metric="loss", operator="le", limit=0.0)],
        corners=[
            DesignCorner(name="nominal"),
            DesignCorner(name="hot", environment={"temperature_c": 125.0}),
        ],
        iterations=12,
        seed=42,
        yield_samples=8,
        require_model_validation=True,
        require_independent_backend=True,
    )


def test_model_aware_optimization_is_reproducible_and_topology_preserving() -> None:
    first = optimize_circuit(_problem(), _screen, validation_evaluator=_validate)
    second = optimize_circuit(_problem(), _screen, validation_evaluator=_validate)

    assert first.best_values == second.best_values == {"components.R1.value": 3.0}
    assert [item.values for item in first.screening_trace] == [
        item.values for item in second.screening_trace
    ]
    assert first.final_document.connectivity_signature() == _document().connectivity_signature()
    assert first.report.status == "simulator_validated"
    assert first.report.independent_validation
    assert first.report.model_hashes == {"R1": "a" * 64, "R2": "b" * 64}
    assert first.report.yield_estimate
    assert first.report.yield_estimate.yield_fraction == 1.0
    assert first.report.yield_estimate.used_selected_models
    assert all(
        result.model_hashes_used == first.report.model_hashes
        for result in first.validation_results.values()
    )

    markdown = render_design_change_report(first)
    assert "simulator_validated" in markdown
    assert "components.R1.value" in markdown
    assert "aaaaaaaa" in markdown
    assert "Validation metrics by corner" in markdown
    assert "headroom" in markdown


def test_model_hash_mismatch_blocks_validated_claim() -> None:
    def bad_validator(document: CircuitDocument, corner: DesignCorner) -> EvaluationResult:
        result = _validate(document, corner)
        return result.model_copy(update={"model_hashes_used": {"R1": "c" * 64}})

    with pytest.raises(ValueError, match="model attestation mismatch"):
        optimize_circuit(_problem(), _screen, validation_evaluator=bad_validator)


def test_same_backend_is_not_independent_validation() -> None:
    def same_backend(document: CircuitDocument, corner: DesignCorner) -> EvaluationResult:
        result = _validate(document, corner)
        return result.model_copy(update={"backend": "analytical"})

    with pytest.raises(ValueError, match="independent validation"):
        optimize_circuit(_problem(), _screen, validation_evaluator=same_backend)


def test_required_independent_validation_needs_a_validator() -> None:
    with pytest.raises(ValueError, match="no validation evaluator"):
        optimize_circuit(_problem(), _screen)


def test_fixed_exact_model_value_cannot_be_varied() -> None:
    document = _document()
    assert document.components[0].model is not None
    document.components[0].model = document.components[0].model.model_copy(
        update={"model_kind": "subckt"}
    )
    with pytest.raises(ValueError, match="cannot vary the nominal value"):
        OptimizationProblem(
            document=document,
            variables=[
                OptimizationVariable(
                    path="components.R1.value",
                    lower=1.0,
                    upper=5.0,
                )
            ],
            objectives=[MetricObjective(metric="loss", goal="minimize")],
        )


def test_feasible_candidate_always_beats_better_infeasible_objective() -> None:
    def evaluator(document: CircuitDocument, corner: DesignCorner) -> EvaluationResult:
        del corner
        value = _value(document, "R1")
        return EvaluationResult(
            metrics={
                "objective": 1e9 if value == 1.0 else 0.0,
                "constraint": 0.0 if value == 1.0 else 1.0,
            },
            backend="analytical",
            method="constraint-priority-fixture",
        )

    problem = OptimizationProblem(
        document=_document(),
        variables=[
            OptimizationVariable(
                path="components.R1.value",
                choices=[1.0, 3.0],
                initial=1.0,
            )
        ],
        objectives=[MetricObjective(metric="objective", goal="minimize")],
        constraints=[MetricConstraint(metric="constraint", operator="le", limit=0.0)],
        iterations=8,
        seed=2,
        require_independent_backend=False,
    )

    result = optimize_circuit(problem, evaluator)
    assert result.best_values == {"components.R1.value": 1.0}
    assert result.report.constraints_passed


def test_progress_and_cancellation_are_cooperative() -> None:
    updates: list[tuple[int, int]] = []
    result = optimize_circuit(
        _problem(),
        _screen,
        validation_evaluator=_validate,
        progress=lambda completed, total: updates.append((completed, total)),
    )
    assert result.report.yield_estimate is not None
    total = result.report.candidates_evaluated + 1 + result.report.yield_estimate.samples
    assert updates[-1] == (
        total,
        total,
    )

    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls > 2

    with pytest.raises(RuntimeError, match="cancelled"):
        optimize_circuit(
            _problem(),
            _screen,
            validation_evaluator=_validate,
            cancel_requested=cancelled,
        )
