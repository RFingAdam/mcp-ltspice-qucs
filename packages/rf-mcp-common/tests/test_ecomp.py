"""Tests for E-series snapping."""

from __future__ import annotations

import pytest

from rf_mcp_common.ecomp import ESeries, snap_to_eseries


def test_snap_already_on_e24_is_lossless() -> None:
    res = snap_to_eseries(4.7e-9, ESeries.E24)
    assert res.snapped == pytest.approx(4.7e-9)
    assert res.error_pct == pytest.approx(0.0, abs=1e-9)


def test_snap_picks_nearest_e24() -> None:
    # 4.137 nH is between 3.9 and 4.3; 4.3 is closer.
    res = snap_to_eseries(4.137e-9, ESeries.E24)
    assert res.snapped == pytest.approx(4.3e-9)


def test_snap_e96_finer_than_e24() -> None:
    val = 4.137e-9
    e24 = snap_to_eseries(val, ESeries.E24)
    e96 = snap_to_eseries(val, ESeries.E96)
    assert abs(e96.error_pct) <= abs(e24.error_pct)


def test_snap_handles_picofarads_and_nanohenries() -> None:
    # Picofarad-scale snap
    res = snap_to_eseries(2.7e-12, ESeries.E24)
    assert res.snapped == pytest.approx(2.7e-12)

    # Nanohenry-scale snap
    res = snap_to_eseries(8.2e-9, ESeries.E24)
    assert res.snapped == pytest.approx(8.2e-9)


def test_snap_zero_or_negative_rejected() -> None:
    with pytest.raises(ValueError):
        snap_to_eseries(0.0)
    with pytest.raises(ValueError):
        snap_to_eseries(-1e-9)


def test_snap_string_series_works() -> None:
    res = snap_to_eseries(1.0e-9, "E24")
    assert res.snapped == pytest.approx(1.0e-9)


def test_snap_decade_boundaries() -> None:
    # 9.5 should round to 9.1 (E24) or 10 in next decade — closer to 9.1
    # but 9.5 - 9.1 = 0.4 vs 10 - 9.5 = 0.5 → 9.1 wins
    res = snap_to_eseries(9.5e-9, ESeries.E24)
    assert res.snapped == pytest.approx(9.1e-9)
