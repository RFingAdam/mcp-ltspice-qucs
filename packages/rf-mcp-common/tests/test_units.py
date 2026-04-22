"""Tests for unit conversion helpers."""

from __future__ import annotations

import math

import pytest

from rf_mcp_common.units import FreqUnit, db, dbm_to_w, hz, lin, w_to_dbm


def test_hz_converts_known_units() -> None:
    assert hz(1.0, FreqUnit.HZ) == 1.0
    assert hz(1.0, FreqUnit.KHZ) == 1e3
    assert hz(2.4, FreqUnit.GHZ) == 2.4e9
    assert hz(928.0, FreqUnit.MHZ) == 928e6


def test_hz_accepts_string_unit() -> None:
    assert hz(1.0, "GHz") == 1e9


def test_db_lin_round_trip() -> None:
    assert math.isclose(lin(db(0.5)), 0.5, rel_tol=1e-9)
    assert math.isclose(db(lin(-3.0)), -3.0, rel_tol=1e-9)


def test_db_of_zero_is_neg_inf() -> None:
    assert db(0) == float("-inf")


def test_dbm_w_round_trip() -> None:
    for dbm in (-130.0, -97.0, 0.0, 23.0, 30.0):
        assert math.isclose(w_to_dbm(dbm_to_w(dbm)), dbm, rel_tol=1e-9)


def test_invalid_unit_raises() -> None:
    with pytest.raises(ValueError):
        hz(1.0, "PHz")
