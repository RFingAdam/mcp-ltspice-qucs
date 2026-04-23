"""Tests for the compare_filter_orders tool."""

from __future__ import annotations

import pytest

from mcp_ltspice.compare import compare_filter_orders
from mcp_ltspice.eval import FilterSpec


@pytest.fixture
def lpf_spec() -> FilterSpec:
    """A low-stress LPF spec (any order should pass)."""
    return FilterSpec.model_validate(
        {
            "passband": {
                "f_start": 1e6,
                "f_stop": 600e6,
                "il_max_db": 1.5,
                "rl_min_db": 8,
            },
            "stopband_targets": [
                {"freq": 2e9, "rejection_min_db": 25, "label": "2x fc"},
                {"freq": 3e9, "rejection_min_db": 35, "label": "3x fc"},
            ],
        }
    )


def test_compare_runs_all_requested_orders(lpf_spec) -> None:
    res = compare_filter_orders(
        orders=[5, 7],
        cutoff_hz=1e9,
        spec=lpf_spec,
        zero_targets_hz=[2e9, 3e9],
        mc_n_runs=50,  # keep test fast
        optimize_max_iter=200,
    )
    assert res.orders_evaluated == [5, 7]
    assert len(res.results) == 2
    assert {r.order for r in res.results} == {5, 7}


def test_compare_picks_a_winner(lpf_spec) -> None:
    res = compare_filter_orders(
        orders=[5, 7],
        cutoff_hz=1e9,
        spec=lpf_spec,
        zero_targets_hz=[2e9, 3e9],
        mc_n_runs=50,
        optimize_max_iter=200,
    )
    assert res.winner_order in (5, 7)
    assert res.winner_rationale


def test_compare_each_result_has_complete_metrics(lpf_spec) -> None:
    res = compare_filter_orders(
        orders=[5],
        cutoff_hz=1e9,
        spec=lpf_spec,
        zero_targets_hz=[2e9, 3e9],
        mc_n_runs=20,
        optimize_max_iter=100,
    )
    r = res.results[0]
    assert r.order == 5
    assert r.n_components > 0
    assert r.spec_overall in ("pass", "fail")
    assert r.srf_severity in ("ok", "caution", "critical")
    assert 0 <= r.mc_yield_pct <= 100
    assert isinstance(r.score, int)
    assert isinstance(r.criteria, list)


def test_compare_with_s2p_dir_writes_files(tmp_path, lpf_spec) -> None:
    res = compare_filter_orders(
        orders=[5],
        cutoff_hz=1e9,
        spec=lpf_spec,
        zero_targets_hz=[2e9, 3e9],
        mc_n_runs=20,
        optimize_max_iter=100,
        s2p_dir=str(tmp_path),
    )
    s2p_path = res.results[0].s2p_path
    assert s2p_path is not None
    from pathlib import Path

    assert Path(s2p_path).exists()
    assert "order5" in Path(s2p_path).name


def test_compare_empty_orders_raises(lpf_spec) -> None:
    with pytest.raises(ValueError, match="at least one order"):
        compare_filter_orders(
            orders=[],
            cutoff_hz=1e9,
            spec=lpf_spec,
            zero_targets_hz=[2e9],
        )


def test_compare_zero_targets_truncated_per_order(lpf_spec) -> None:
    """If zero_targets_hz has more entries than the order has traps, the
    extras are silently ignored."""
    res = compare_filter_orders(
        orders=[5],  # 5th-order has only 2 traps
        cutoff_hz=1e9,
        spec=lpf_spec,
        zero_targets_hz=[2e9, 3e9, 4e9, 5e9],  # 4 targets, only 2 used
        mc_n_runs=20,
        optimize_max_iter=100,
    )
    assert res.results[0].n_traps_used == 2
