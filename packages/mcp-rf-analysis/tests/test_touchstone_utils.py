"""Tests for Touchstone diff / delay / fitting utilities."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mcp_rf_analysis.touchstone_utils import (
    compare_sparameters,
    extract_delay,
    fit_equivalent_circuit,
)
from rf_mcp_common.touchstone import network_to_touchstone


def _write_s21(path: Path, frequencies: list[float], s21: list[complex]) -> Path:
    freq = np.asarray(frequencies, dtype=float)
    values = np.asarray(s21, dtype=np.complex128)
    s = np.zeros((freq.size, 2, 2), dtype=np.complex128)
    s[:, 1, 0] = values
    s[:, 0, 1] = values
    return network_to_touchstone(freq, s, path)


def test_compare_identical_files_zero_diff(lpf_s2p) -> None:
    res = compare_sparameters(lpf_s2p, lpf_s2p, metric="s21_db")
    assert res["max_abs_diff"] < 1e-9
    assert res["rms_diff"] < 1e-9


def test_compare_unknown_metric_raises(lpf_s2p) -> None:
    with pytest.raises(ValueError):
        compare_sparameters(lpf_s2p, lpf_s2p, metric="bogus")


def test_compare_shared_endpoints_does_not_hide_interior_difference(tmp_path: Path) -> None:
    a = _write_s21(
        tmp_path / "a.s2p",
        [1e9, 2e9, 3e9],
        [0.5 + 0j, 0.5 + 0j, 0.5 + 0j],
    )
    b = _write_s21(
        tmp_path / "b.s2p",
        [1e9, 1.5e9, 2.5e9, 3e9],
        [0.5 + 0j, 0.1 + 0j, 0.1 + 0j, 0.5 + 0j],
    )

    result = compare_sparameters(a, b, metric="mag_s21")

    assert result["frequency_policy"] == "union_within_overlap"
    assert result["freq_hz"] == [1e9, 1.5e9, 2e9, 2.5e9, 3e9]
    assert result["max_abs_diff"] == pytest.approx(0.4)


def test_compare_partial_overlap_never_extrapolates(tmp_path: Path) -> None:
    a = _write_s21(
        tmp_path / "a.s2p",
        [1e9, 2e9, 2.5e9, 3e9],
        [0.8 + 0j] * 4,
    )
    b = _write_s21(
        tmp_path / "b.s2p",
        [2e9, 2.25e9, 3e9, 4e9],
        [0.6 + 0j] * 4,
    )

    result = compare_sparameters(a, b, metric="mag_s21")

    assert min(result["freq_hz"]) == pytest.approx(2e9)
    assert max(result["freq_hz"]) == pytest.approx(3e9)
    assert result["overlap_hz"] == [2e9, 3e9]
    assert result["interpolation"]["extrapolation"] is False
    assert result["max_abs_diff"] == pytest.approx(0.2)


def test_compare_disjoint_ranges_raises_instead_of_extrapolating(tmp_path: Path) -> None:
    a = _write_s21(tmp_path / "a.s2p", [1e9, 2e9], [0.5 + 0j, 0.5 + 0j])
    b = _write_s21(tmp_path / "b.s2p", [3e9, 4e9], [0.5 + 0j, 0.5 + 0j])

    with pytest.raises(ValueError, match="do not overlap"):
        compare_sparameters(a, b)


def test_compare_phase_uses_shortest_circular_difference(tmp_path: Path) -> None:
    a_value = complex(np.exp(1j * np.deg2rad(179.0)))
    b_value = complex(np.exp(1j * np.deg2rad(-179.0)))
    a = _write_s21(tmp_path / "a.s2p", [1e9, 2e9], [a_value, a_value])
    b = _write_s21(tmp_path / "b.s2p", [1e9, 2e9], [b_value, b_value])

    result = compare_sparameters(a, b, metric="phase_s21_deg")

    assert result["phase_difference_mode"] == "circular_shortest_degrees"
    assert result["diff"] == pytest.approx([2.0, 2.0])
    assert result["max_abs_diff"] == pytest.approx(2.0)


def test_extract_group_delay_returns_arrays(lpf_s2p) -> None:
    res = extract_delay(lpf_s2p, method="group_delay")
    assert "group_delay_s" in res
    assert len(res["group_delay_s"]) == len(res["freq_hz"])
    assert "band_mean_delay_s" not in res
    assert res["warnings"]


def test_extract_group_delay_requires_explicit_band_for_summary(lpf_s2p) -> None:
    res = extract_delay(
        lpf_s2p,
        method="group_delay",
        analysis_band_hz=(1e6, 5e8),
    )
    assert np.isfinite(res["band_mean_delay_s"])
    assert res["analysis_band_hz"] == [1e6, 5e8]


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
    assert fit["solver"]["success"] is True
    assert fit["fit_quality"]["normalized_rmse"] < 1e-3
    assert fit["fit_quality"]["identifiable"] is True
    assert fit["valid_frequency_hz"] == pytest.approx([f[0], f[-1]])


def test_fit_unknown_topology_raises(lpf_s2p) -> None:
    with pytest.raises(ValueError):
        fit_equivalent_circuit(lpf_s2p, topology="not_a_real_topology")
