"""Tests for spec evaluation."""

from __future__ import annotations

import numpy as np
import pytest

from mcp_ltspice.eval import FilterSpec, evaluate_filter_spec
from mcp_ltspice.extract import (
    components_dict_to_elements,
    ladder_sparams_from_components,
)
from mcp_ltspice.synthesis import synthesize_lc_lpf
from rf_mcp_common.touchstone import network_to_touchstone


def _write_synth_s2p(tmp_path, design, transmission_zeros: bool):
    f = np.geomspace(1e6, 5e9, 1001)
    elements = components_dict_to_elements(
        design.components,
        topology="series_first",
        transmission_zeros=transmission_zeros,
    )
    s = ladder_sparams_from_components(elements, f, z0=design.z0)
    return network_to_touchstone(f, s, tmp_path / "design.s2p", z0=design.z0)


def test_passband_il_pass(tmp_path) -> None:
    # 5th-order Butterworth at 1 GHz; passband 0-500 MHz IL should be tiny
    design = synthesize_lc_lpf("butterworth", order=5, cutoff_hz=1e9)
    s2p = _write_synth_s2p(tmp_path, design, transmission_zeros=False)
    spec = FilterSpec.model_validate(
        {
            "passband": {
                "f_start": 1e6,
                "f_stop": 500e6,
                "il_max_db": 0.5,
                "rl_min_db": 15,
            },
            "stopband_targets": [],
        }
    )
    result = evaluate_filter_spec(s2p, spec)
    assert result.overall == "pass"
    il = next(c for c in result.criteria if c.label == "Passband IL")
    rl = next(c for c in result.criteria if c.label == "Passband RL")
    assert il.measured_db < 0.5
    assert il.margin_db > 0
    assert rl.measured_db > 15
    assert rl.margin_db > 0


def test_passband_fails_when_pushed_above_cutoff(tmp_path) -> None:
    design = synthesize_lc_lpf("butterworth", order=5, cutoff_hz=1e9)
    s2p = _write_synth_s2p(tmp_path, design, transmission_zeros=False)
    # Set passband through 1.5 GHz where the filter is rolling off hard
    spec = FilterSpec.model_validate(
        {
            "passband": {
                "f_start": 1e6,
                "f_stop": 1.5e9,
                "il_max_db": 0.5,
                "rl_min_db": 15,
            }
        }
    )
    result = evaluate_filter_spec(s2p, spec)
    assert result.overall == "fail"
    il = next(c for c in result.criteria if c.label == "Passband IL")
    assert il.status == "fail"


def test_stopband_target_pass_and_fail(tmp_path) -> None:
    # Elliptic LPF with explicit notches; we know roughly where they are
    design = synthesize_lc_lpf(
        "elliptic", order=5, cutoff_hz=1e9, ripple_db=0.1, stopband_atten_db=40
    )
    s2p = _write_synth_s2p(tmp_path, design, transmission_zeros=True)
    spec = FilterSpec.model_validate(
        {
            "passband": {
                "f_start": 1e6,
                "f_stop": 500e6,
                "il_max_db": 0.5,
                "rl_min_db": 15,
            },
            "stopband_targets": [
                # Should easily pass — far in stopband
                {"freq": 3e9, "rejection_min_db": 30, "label": "deep stopband"},
                # Should fail — inside passband; rejection is ~0
                {"freq": 100e6, "rejection_min_db": 30, "label": "in passband"},
            ],
        }
    )
    result = evaluate_filter_spec(s2p, spec)
    deep = next(c for c in result.criteria if c.label == "deep stopband")
    inside = next(c for c in result.criteria if c.label == "in passband")
    assert deep.status == "pass"
    assert inside.status == "fail"


def test_evaluate_with_dict_spec(tmp_path) -> None:
    design = synthesize_lc_lpf("butterworth", order=3, cutoff_hz=1e9)
    s2p = _write_synth_s2p(tmp_path, design, transmission_zeros=False)
    spec_dict = {
        "passband": {
            "f_start": 1e6,
            "f_stop": 500e6,
            "il_max_db": 1.0,
            "rl_min_db": 10,
        },
    }
    result = evaluate_filter_spec(s2p, spec_dict)
    assert result.overall == "pass"


def test_passband_outside_sweep_raises(tmp_path) -> None:
    design = synthesize_lc_lpf("butterworth", order=3, cutoff_hz=1e9)
    s2p = _write_synth_s2p(tmp_path, design, transmission_zeros=False)
    spec = FilterSpec.model_validate(
        {
            "passband": {
                "f_start": 100e9,
                "f_stop": 200e9,
                "il_max_db": 1.0,
                "rl_min_db": 10,
            }
        }
    )
    with pytest.raises(ValueError, match="outside Touchstone sweep"):
        evaluate_filter_spec(s2p, spec)
