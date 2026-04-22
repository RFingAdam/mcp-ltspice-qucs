"""Tests for Phase 3 mcp-ltspice tools: vendor models, find_zeros,
optimizer, Monte Carlo, stability."""

from __future__ import annotations

import math

import numpy as np
import pytest
from mcp_ltspice.eval import FilterSpec
from mcp_ltspice.extract import (
    components_dict_to_elements,
    ladder_sparams_from_components,
)
from mcp_ltspice.find_zeros import find_transmission_zeros
from mcp_ltspice.montecarlo import monte_carlo_analysis
from mcp_ltspice.optimize import optimize_filter
from mcp_ltspice.stability import stability_check
from mcp_ltspice.synthesis import (
    place_transmission_zero,
    synthesize_lc_lpf,
)
from mcp_ltspice.vendor_models import (
    list_vendor_parts,
    lookup_part,
    substitute_real_components,
)
from rf_mcp_common.touchstone import network_to_touchstone


# --------------------------------------------------------------------------
# Vendor models
# --------------------------------------------------------------------------


def test_list_vendor_parts_coilcraft_0402hp() -> None:
    parts = list_vendor_parts("coilcraft_0402hp")
    assert len(parts) > 5
    # Smallest is 1 nH
    assert min(parts) == pytest.approx(1e-9)


def test_lookup_part_picks_nearest_inductor() -> None:
    part = lookup_part("coilcraft_0402hp", 4.5e-9, kind="L")
    # Closest available is 4.7 nH
    assert part.L_h == pytest.approx(4.7e-9)
    # SRF is in GHz range
    assert 1e9 < part.srf_hz < 20e9


def test_lookup_part_capacitor() -> None:
    part = lookup_part("murata_gjm_c0g", 2.0e-12, kind="C")
    # Closest is 1.8 or 2.2 pF
    assert part.C_f in (1.8e-12, 2.2e-12)


def test_lookup_part_wrong_kind_raises() -> None:
    with pytest.raises(ValueError):
        lookup_part("coilcraft_0402hp", 1e-12, kind="C")


def test_substitute_real_components_emits_parasitic_data() -> None:
    comps = {"L1": 4.7e-9, "C2": 2.2e-12, "L3": 4.7e-9}
    out = substitute_real_components(comps)
    assert out["L1"]["kind"] == "L"
    assert out["C2"]["kind"] == "C"
    assert out["L1"]["Cp"] > 0  # parasitic shunt cap is present
    assert out["C2"]["Ls"] > 0  # parasitic ESL is present
    assert out["L1"]["srf_hz"] > 0


# --------------------------------------------------------------------------
# find_transmission_zeros
# --------------------------------------------------------------------------


def test_find_transmission_zeros_in_elliptic(tmp_path) -> None:
    design = synthesize_lc_lpf(
        "elliptic", order=5, cutoff_hz=1e9, ripple_db=0.1, stopband_atten_db=40
    )
    # Place its 2 traps at known frequencies
    comps = place_transmission_zero(
        design.components, trap_index=2, target_freq_hz=1.5e9, snap_series=None
    )["components"]
    comps = place_transmission_zero(
        comps, trap_index=4, target_freq_hz=2.0e9, snap_series=None
    )["components"]
    # Build s2p
    f = np.geomspace(0.5e9, 5e9, 1001)
    elements = components_dict_to_elements(comps, transmission_zeros=True)
    s = ladder_sparams_from_components(elements, f, z0=50.0)
    s2p = network_to_touchstone(f, s, tmp_path / "test.s2p")

    zeros = find_transmission_zeros(s2p, min_depth_db=20)
    found_freqs = sorted(z["freq_hz"] for z in zeros)
    assert len(zeros) >= 2
    assert any(abs(f0 - 1.5e9) / 1.5e9 < 0.02 for f0 in found_freqs)
    assert any(abs(f0 - 2.0e9) / 2.0e9 < 0.02 for f0 in found_freqs)


def test_find_zeros_respects_freq_window(tmp_path, lpf_s2p_for_zeros) -> None:
    zeros_all = find_transmission_zeros(lpf_s2p_for_zeros, min_depth_db=20)
    zeros_low = find_transmission_zeros(
        lpf_s2p_for_zeros, min_depth_db=20, f_max_hz=1.7e9
    )
    assert len(zeros_low) <= len(zeros_all)


@pytest.fixture
def lpf_s2p_for_zeros(tmp_path):
    design = synthesize_lc_lpf(
        "elliptic", order=5, cutoff_hz=1e9, ripple_db=0.1, stopband_atten_db=40
    )
    comps = place_transmission_zero(
        design.components, trap_index=2, target_freq_hz=1.5e9, snap_series=None
    )["components"]
    comps = place_transmission_zero(
        comps, trap_index=4, target_freq_hz=2.0e9, snap_series=None
    )["components"]
    f = np.geomspace(0.5e9, 5e9, 1001)
    elements = components_dict_to_elements(comps, transmission_zeros=True)
    s = ladder_sparams_from_components(elements, f, z0=50.0)
    return network_to_touchstone(f, s, tmp_path / "ellip.s2p")


# --------------------------------------------------------------------------
# optimize_filter
# --------------------------------------------------------------------------


def test_optimize_improves_loss_or_already_passing() -> None:
    design = synthesize_lc_lpf(
        "elliptic", order=5, cutoff_hz=1e9, ripple_db=0.1, stopband_atten_db=40
    )
    spec = FilterSpec.model_validate(
        {
            "passband": {
                "f_start": 1e6, "f_stop": 800e6, "il_max_db": 0.5, "rl_min_db": 12,
            },
            "stopband_targets": [
                {"freq": 2e9, "rejection_min_db": 30, "label": "2H"},
            ],
        }
    )
    res = optimize_filter(
        design.components, spec, transmission_zeros=True,
        max_iter=200, snap_series=None,
    )
    # Either it converges to zero loss or it stays at zero loss (already passing)
    assert res.final_loss <= res.initial_loss + 1e-6


def test_optimize_with_e24_snap_returns_snapped_values() -> None:
    design = synthesize_lc_lpf("butterworth", order=3, cutoff_hz=500e6)
    spec = {
        "passband": {
            "f_start": 1e6, "f_stop": 250e6, "il_max_db": 0.5, "rl_min_db": 15,
        },
    }
    res = optimize_filter(
        design.components, spec, transmission_zeros=False,
        snap_series="E24", max_iter=50,
    )
    # Snapped values should differ from continuous-optimized in general
    assert res.snapped_components.keys() == design.components.keys()


# --------------------------------------------------------------------------
# monte_carlo_analysis
# --------------------------------------------------------------------------


def test_monte_carlo_returns_yield_and_stats() -> None:
    design = synthesize_lc_lpf("butterworth", order=3, cutoff_hz=500e6)
    spec = {
        "passband": {
            "f_start": 1e6, "f_stop": 250e6, "il_max_db": 0.5, "rl_min_db": 15,
        },
    }
    res = monte_carlo_analysis(
        design.components, spec, tolerance_pct=5.0,
        n_runs=50, n_jobs=1, transmission_zeros=False,
    )
    assert res.n_runs == 50
    assert 0 <= res.yield_pct <= 100
    assert "passband_il_db" in res.per_metric_stats
    assert "mean" in res.per_metric_stats["passband_il_db"]


def test_monte_carlo_per_refdes_tolerance() -> None:
    design = synthesize_lc_lpf("butterworth", order=3, cutoff_hz=500e6)
    spec = {
        "passband": {
            "f_start": 1e6, "f_stop": 250e6, "il_max_db": 0.5, "rl_min_db": 15,
        },
    }
    tol = {r: 2.0 for r in design.components}
    res = monte_carlo_analysis(
        design.components, spec, tolerance_pct=tol,
        n_runs=20, n_jobs=1, transmission_zeros=False,
    )
    assert res.n_runs == 20


# --------------------------------------------------------------------------
# stability_check
# --------------------------------------------------------------------------


def test_stability_check_returns_arrays(lpf_s2p_for_zeros) -> None:
    res = stability_check(lpf_s2p_for_zeros)
    assert "k_factor" in res
    assert "delta_mag" in res
    assert "mu_factor" in res
    assert isinstance(res["unconditionally_stable"], bool)
    assert math.isfinite(res["min_k"])
    assert math.isfinite(res["max_delta"])
