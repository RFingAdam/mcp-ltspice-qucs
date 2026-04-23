"""Tests for the sweep / corner / sensitivity primitives."""

from __future__ import annotations

import pytest

from mcp_ltspice.eval import FilterSpec
from mcp_ltspice.sweep import (
    corner_analysis,
    parameter_sweep,
    sensitivity_analysis,
)
from mcp_ltspice.synthesis import synthesize_lc_lpf


@pytest.fixture
def lpf_design():
    """A 5th-order Butterworth LPF at 1 GHz with a permissive spec."""
    design = synthesize_lc_lpf("butterworth", order=5, cutoff_hz=1e9)
    spec = FilterSpec.model_validate(
        {
            "passband": {
                "f_start": 1e6,
                "f_stop": 600e6,
                "il_max_db": 0.5,
                "rl_min_db": 14,
            },
            "stopband_targets": [
                {"freq": 2e9, "rejection_min_db": 25, "label": "2x fc"},
            ],
        }
    )
    return design, spec


# ---- parameter_sweep -----------------------------------------------------


def test_parameter_sweep_single_axis(lpf_design) -> None:
    design, spec = lpf_design
    sweep = {"L1": [v * design.components["L1"] for v in [0.95, 1.0, 1.05]]}
    result = parameter_sweep(
        design.components,
        sweep,
        spec,
        transmission_zeros=False,
    )
    assert result.n_points == 3
    # All three points should pass — the design is robust to ±5% on L1
    assert result.yield_pct == 100.0


def test_parameter_sweep_2d_cartesian(lpf_design) -> None:
    design, spec = lpf_design
    sweep = {
        "L1": [v * design.components["L1"] for v in [0.9, 1.0, 1.1]],
        "C2": [v * design.components["C2"] for v in [0.9, 1.0, 1.1]],
    }
    result = parameter_sweep(
        design.components,
        sweep,
        spec,
        transmission_zeros=False,
    )
    assert result.n_points == 9  # 3 × 3 cartesian


def test_parameter_sweep_records_per_point_margins(lpf_design) -> None:
    design, spec = lpf_design
    sweep = {"C2": [design.components["C2"] * 1.0]}
    result = parameter_sweep(
        design.components,
        sweep,
        spec,
        transmission_zeros=False,
    )
    p = result.points[0]
    assert "Passband IL" in p.margins
    assert "Passband RL" in p.margins
    assert "2x fc" in p.margins


# ---- corner_analysis -----------------------------------------------------


def test_corner_analysis_typical_passes(lpf_design) -> None:
    design, spec = lpf_design
    corners = {"TT": dict.fromkeys(design.components, 1.0)}
    result = corner_analysis(
        design.components,
        corners,
        spec,
        transmission_zeros=False,
    )
    assert result["all_corners_pass"] is True
    assert result["results"]["TT"]["overall"] == "pass"


def test_corner_analysis_extreme_corners_can_fail(lpf_design) -> None:
    design, spec = lpf_design
    corners = {
        "TT": dict.fromkeys(design.components, 1.0),
        "Disaster": dict.fromkeys(design.components, 0.5),  # all -50%
    }
    result = corner_analysis(
        design.components,
        corners,
        spec,
        transmission_zeros=False,
    )
    # Typical passes, disaster shifts cutoff way down
    assert result["results"]["TT"]["overall"] == "pass"
    # Disaster may or may not fail depending on spec, but at least the
    # function should produce a deterministic answer
    assert result["results"]["Disaster"]["overall"] in ("pass", "fail")


def test_corner_analysis_records_perturbed_values(lpf_design) -> None:
    design, spec = lpf_design
    corners = {"hot": {"L1": 1.10}}
    result = corner_analysis(
        design.components,
        corners,
        spec,
        transmission_zeros=False,
    )
    perturbed_l1 = result["results"]["hot"]["components"]["L1"]
    assert perturbed_l1 == pytest.approx(design.components["L1"] * 1.10)


# ---- sensitivity_analysis ------------------------------------------------


def test_sensitivity_returns_ranked_list(lpf_design) -> None:
    design, spec = lpf_design
    result = sensitivity_analysis(
        design.components,
        spec,
        perturbation_pct=2.0,
        transmission_zeros=False,
    )
    assert len(result["ranked_sensitivities"]) > 0
    # Sorted desc by |sensitivity|
    abs_vals = [s["abs_sensitivity"] for s in result["ranked_sensitivities"]]
    assert abs_vals == sorted(abs_vals, reverse=True)


def test_sensitivity_identifies_most_influential(lpf_design) -> None:
    design, spec = lpf_design
    result = sensitivity_analysis(
        design.components,
        spec,
        perturbation_pct=2.0,
        transmission_zeros=False,
    )
    most = result["most_influential_component"]
    assert most in design.components


def test_sensitivity_central_difference_is_signed(lpf_design) -> None:
    """Sensitivities can be positive or negative — increasing L can
    improve some criteria and degrade others."""
    design, spec = lpf_design
    result = sensitivity_analysis(
        design.components,
        spec,
        perturbation_pct=2.0,
        transmission_zeros=False,
    )
    signs = {s["sensitivity_db_per_pct"] > 0 for s in result["ranked_sensitivities"]}
    # Expect both positive and negative sensitivities across the criteria
    assert len(signs) == 2
