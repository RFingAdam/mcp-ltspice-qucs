"""Regression matrix for typed filter context and mandatory grid coverage."""

from __future__ import annotations

import numpy as np
import pytest

from mcp_ltspice.analysis_context import (
    FilterAnalysisContext,
    build_spec_frequency_grid,
    evaluate_component_margins,
    validate_spec_frequency_coverage,
)
from mcp_ltspice.eval import FilterSpec
from mcp_ltspice.montecarlo import monte_carlo_analysis
from mcp_ltspice.optimize import optimize_filter
from mcp_ltspice.sweep import parameter_sweep, sensitivity_analysis
from mcp_ltspice.synthesis import (
    Topology,
    synthesize_lc_bpf,
    synthesize_lc_bsf,
    synthesize_lc_hpf,
    synthesize_lc_lpf,
)
from mcp_ltspice.vendor_models import substitute_real_components


def _case(kind: str, topology: Topology):
    if kind == "lowpass":
        design = synthesize_lc_lpf("butterworth", 3, 1e9, topology=topology)
        passband = (10e6, 500e6)
        targets = [(2e9, "upper stop")]
    elif kind == "highpass":
        design = synthesize_lc_hpf("butterworth", 3, 1e9, topology=topology)
        passband = (1.5e9, 3e9)
        targets = [(200e6, "lower stop")]
    elif kind == "bandpass":
        design = synthesize_lc_bpf("butterworth", 3, 800e6, 1.2e9, topology=topology)
        passband = (850e6, 1.15e9)
        targets = [(300e6, "lower stop"), (2e9, "upper stop")]
    else:
        design = synthesize_lc_bsf("butterworth", 3, 800e6, 1.2e9, topology=topology)
        passband = (100e6, 500e6)
        targets = [(1e9, "notch")]
    spec = FilterSpec.model_validate(
        {
            "passband": {
                "f_start": passband[0],
                "f_stop": passband[1],
                "il_max_db": 100.0,
                "rl_min_db": 0.01,
            },
            "stopband_targets": [
                {"freq": freq, "rejection_min_db": 0.01, "label": label} for freq, label in targets
            ],
        }
    )
    return design, spec


@pytest.mark.parametrize("kind", ["lowpass", "highpass", "bandpass", "bandstop"])
@pytest.mark.parametrize("topology", list(Topology))
def test_all_filter_kinds_and_topologies_preserve_context(kind, topology) -> None:
    design, spec = _case(kind, topology)
    first_ref = next(iter(design.components))
    sweep = parameter_sweep(
        design.components,
        {first_ref: [design.components[first_ref]]},
        spec,
        transmission_zeros=None,
        kind=kind,
        topology=topology,
        transmission_zeros_hz=design.transmission_zeros_hz,
    )

    assert sweep.analysis_context["kind"] == kind
    assert sweep.analysis_context["topology"] == topology.value
    assert set(sweep.points[0].margins) == {
        "Passband IL",
        "Passband RL",
        *(target.label for target in spec.stopband_targets),
    }
    assert all(np.isfinite(value) for value in sweep.points[0].margins.values())

    mc = monte_carlo_analysis(
        design.components,
        spec,
        tolerance_pct=0.0,
        n_runs=2,
        n_jobs=1,
        transmission_zeros=None,
        kind=kind,
        topology=topology,
        transmission_zeros_hz=design.transmission_zeros_hz,
    )
    assert mc.analysis_context["kind"] == kind
    assert mc.analysis_context["topology"] == topology.value

    sensitivity = sensitivity_analysis(
        design.components,
        spec,
        perturbation_pct=1.0,
        transmission_zeros=None,
        kind=kind,
        topology=topology,
        transmission_zeros_hz=design.transmission_zeros_hz,
    )
    assert sensitivity["analysis_context"]["kind"] == kind


@pytest.mark.parametrize("kind", ["lowpass", "highpass", "bandpass", "bandstop"])
@pytest.mark.parametrize("topology", list(Topology))
def test_optimizer_preserves_filter_context(kind, topology) -> None:
    design, spec = _case(kind, topology)
    first_ref = next(iter(design.components))
    result = optimize_filter(
        design.components,
        spec,
        tune=[first_ref],
        transmission_zeros=None,
        kind=kind,
        topology=topology,
        max_iter=1,
        snap_series=None,
        transmission_zeros_hz=design.transmission_zeros_hz,
    )
    assert result.analysis_context["kind"] == kind
    assert result.analysis_context["topology"] == topology.value
    assert {item["label"] for item in result.margins_final} == {
        "Passband IL",
        "Passband RL",
        *(target.label for target in spec.stopband_targets),
    }


def test_lower_stopband_target_is_inserted_exactly() -> None:
    design, spec = _case("highpass", Topology.SHUNT_FIRST)
    grid = build_spec_frequency_grid(spec, npoints=41)
    lower_target = spec.stopband_targets[0].freq
    assert lower_target in grid
    assert grid[0] <= lower_target < spec.passband.f_start

    context = FilterAnalysisContext.create(
        kind="highpass",
        topology="shunt_first",
        transmission_zeros=False,
    )
    result = evaluate_component_margins(design.components, spec, context=context, f_grid=grid)
    assert np.isfinite(result.margins["lower stop"])


def test_missing_required_criterion_is_a_hard_error() -> None:
    _, spec = _case("highpass", Topology.SERIES_FIRST)
    with pytest.raises(ValueError, match="required stopband target"):
        validate_spec_frequency_coverage(spec, np.geomspace(1e9, 4e9, 21))


def test_substitution_parasitics_survive_sweep_and_monte_carlo() -> None:
    design, spec = _case("lowpass", Topology.SERIES_FIRST)
    substitution = substitute_real_components(
        design.components,
        max_value_drift_pct=None,
    )
    realized = {
        refdes: float(selected["snapped_value"]) for refdes, selected in substitution.items()
    }
    first_ref = next(iter(realized))

    ideal = parameter_sweep(
        realized,
        {first_ref: [realized[first_ref]]},
        spec,
        transmission_zeros=False,
    )
    modeled = parameter_sweep(
        realized,
        {first_ref: [realized[first_ref]]},
        spec,
        transmission_zeros=False,
        component_substitution=substitution,
    )
    assert modeled.analysis_context["evaluation_mode"] == "approximate_model"
    assert modeled.analysis_context["model_fidelity"] == "first_order_parasitic_reduction"
    assert modeled.analysis_context["component_models"]
    assert modeled.points[0].margins != ideal.points[0].margins

    mc = monte_carlo_analysis(
        realized,
        spec,
        tolerance_pct=1.0,
        n_runs=2,
        n_jobs=1,
        transmission_zeros=False,
        component_substitution=substitution,
    )
    assert mc.analysis_context["evaluation_mode"] == "approximate_model"
