"""Tests for Touchstone diff / delay / fitting utilities."""

from __future__ import annotations

import numpy as np
import pytest

from mcp_rf_analysis.touchstone_utils import (
    compare_sparameters,
    extract_delay,
    fit_equivalent_circuit,
)


def test_compare_identical_files_zero_diff(lpf_s2p) -> None:
    res = compare_sparameters(lpf_s2p, lpf_s2p, metric="s21_db")
    assert res["max_abs_diff"] < 1e-9
    assert res["rms_diff"] < 1e-9


def test_compare_unknown_metric_raises(lpf_s2p) -> None:
    with pytest.raises(ValueError):
        compare_sparameters(lpf_s2p, lpf_s2p, metric="bogus")


def test_extract_group_delay_returns_arrays(lpf_s2p) -> None:
    res = extract_delay(lpf_s2p, method="group_delay")
    assert "group_delay_s" in res
    assert len(res["group_delay_s"]) == len(res["freq_hz"])


def test_extract_unwrapped_phase(lpf_s2p) -> None:
    res = extract_delay(lpf_s2p, method="unwrapped_phase")
    assert "phase_rad" in res
    assert "phase_deg" in res


def test_extract_delay_unknown_method_raises(lpf_s2p) -> None:
    with pytest.raises(ValueError):
        extract_delay(lpf_s2p, method="not_a_thing")


def test_fit_series_l_recovers_value(tmp_path) -> None:
    # Build a known series-L network synthetically
    from mcp_ltspice.extract import (
        ladder_sparams_from_components,
    )
    from rf_mcp_common.touchstone import network_to_touchstone

    f = np.linspace(1e7, 1e9, 201)
    L_true = 10e-9
    s = ladder_sparams_from_components([("series_l", {"L": L_true})], f)
    s2p = network_to_touchstone(f, s, tmp_path / "L.s2p", z0=50.0)
    fit = fit_equivalent_circuit(s2p, topology="series_l")
    assert fit["L"] == pytest.approx(L_true, rel=0.05)


def test_fit_unknown_topology_raises(lpf_s2p) -> None:
    with pytest.raises(ValueError):
        fit_equivalent_circuit(lpf_s2p, topology="not_a_real_topology")
