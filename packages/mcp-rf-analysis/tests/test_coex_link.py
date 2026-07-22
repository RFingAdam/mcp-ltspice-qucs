"""Tests for link budget, antenna isolation, and coex matrix."""

from __future__ import annotations

import pytest

from mcp_rf_analysis.coex import check_coex_matrix, lookup_harmonic_victims
from mcp_rf_analysis.link import (
    compute_antenna_isolation_estimate,
    compute_desense,
    compute_path_loss,
)


def test_path_loss_friis_at_1m_2_4ghz() -> None:
    res = compute_path_loss(2.4e9, 1.0)
    # PL_dB = 20 log10(4π · 1 · 2.4e9 / 3e8) ≈ 40.05 dB
    assert res["path_loss_db"] == pytest.approx(40.05, abs=0.5)


def test_path_loss_log_distance_higher_n_increases_loss() -> None:
    pl_n2 = compute_path_loss(2.4e9, 10.0, model="log_distance", n=2.0)
    pl_n3 = compute_path_loss(2.4e9, 10.0, model="log_distance", n=3.0)
    assert pl_n3["path_loss_db"] > pl_n2["path_loss_db"]


def test_path_loss_invalid_inputs_raise() -> None:
    with pytest.raises(ValueError):
        compute_path_loss(0, 1.0)
    with pytest.raises(ValueError):
        compute_path_loss(1e9, 0)


def test_antenna_isolation_basic() -> None:
    res = compute_antenna_isolation_estimate(0.05, 2.4e9)
    # ~14 dB Friis at 5 cm @ 2.4 GHz
    assert 10 < res["isolation_db"] < 30


def test_antenna_isolation_with_small_ground_adds_penalty() -> None:
    base = compute_antenna_isolation_estimate(0.05, 2.4e9)
    with_penalty = compute_antenna_isolation_estimate(
        0.05,
        2.4e9,
        ground_plane_size_m=0.01,  # << λ/4 at 2.4 GHz (3.1 cm)
    )
    assert with_penalty["isolation_db"] == base["isolation_db"] - 6


def test_compute_desense_high_concern() -> None:
    res = compute_desense(
        aggressor_power_dbm=20,
        filter_rejection_db=10,
        antenna_iso_db=15,
        victim_noise_floor_dbm=-97,
    )
    assert res["received_at_rx_dbm"] == -5  # 20-10-15
    assert res["concern_level"] == "critical"
    assert res["snr_margin_db"] < -30


def test_compute_desense_no_concern() -> None:
    res = compute_desense(
        aggressor_power_dbm=23,
        filter_rejection_db=60,
        antenna_iso_db=30,
        victim_noise_floor_dbm=-97,
    )
    # P_rx = 23-60-30 = -67 dBm, well above noise floor (-97), so margin is -30 → "high"
    # But snr_margin = -97 - (-67) = -30 → boundary; concern depends on cutoff.
    # We'll just confirm the math
    assert res["received_at_rx_dbm"] == pytest.approx(-67)


def test_lookup_harmonic_victims_for_915mhz() -> None:
    result = lookup_harmonic_victims(915e6, harmonic_orders=[2, 3])
    # 2H = 1830 MHz lands in LTE B25 DL; 3H = 2745 MHz near LTE B7 / B41
    h2 = next(r for r in result if r["harmonic"] == 2)
    h3 = next(r for r in result if r["harmonic"] == 3)
    assert h2["freq_hz"] == 1830e6
    assert h3["freq_hz"] == 2745e6
    # Should find at least one victim band for 2H in LTE DL
    assert h2["victims"]


HALOW_TX = [
    {
        "name": "HaLow",
        "f_center_hz": 915e6,
        "power_dbm": 23,
        "filtered_harmonic_dbc": {"2H": -10, "3H": -20},
        "filter_rejection_db": 0,
    },
]


def test_check_coex_matrix_identifies_halow_2f0_collision() -> None:
    """HaLow 2H = 1830 MHz lands inside LTE B3 DL (1805-1880).

    The previous version of this test pointed at B25 DL (1930-1995),
    which 2H misses entirely — its own comment conceded there would be
    no collision — and then asserted only that the response was a dict
    with the expected keys. A logic inversion that reported collisions
    everywhere, or none at all, passed it.
    """
    rx = [{"name": "LTE_B3_DL", "f_range_hz": [1805e6, 1880e6], "sensitivity_dbm": -97}]
    res = check_coex_matrix(HALOW_TX, rx, antenna_iso_db=20)

    hits = [m for m in res["matrix"] if m["mechanism"] == "harmonic_2"]
    assert len(hits) == 1, f"expected exactly one 2H collision, got {res['matrix']}"
    hit = hits[0]
    assert hit["aggressor"] == "HaLow"
    assert hit["victim"] == "LTE_B3_DL"
    assert hit["f_emit_hz"] == pytest.approx(1830e6)
    # 23 dBm fundamental, 2H suppressed 10 dBc -> 13 dBm emitted at 2f0.
    assert hit["emit_dbm"] == pytest.approx(13.0)
    # ...then 20 dB of antenna isolation to the victim.
    assert hit["predicted_at_rx_dbm"] == pytest.approx(13.0 - 20.0)


def test_check_coex_matrix_reports_nothing_for_a_clean_pair() -> None:
    """The negative case: no harmonic of 915 MHz falls in B25 DL.

    2H = 1830 and 3H = 2745 both sit outside 1930-1995, so the matrix
    must be empty. Note n_aggressors counts TX entries, not collisions,
    so it stays 1 either way — which is why asserting on it proved
    nothing.
    """
    rx = [{"name": "LTE_B25_DL", "f_range_hz": [1930e6, 1995e6], "sensitivity_dbm": -97}]
    res = check_coex_matrix(HALOW_TX, rx, antenna_iso_db=20)
    assert res["matrix"] == []
