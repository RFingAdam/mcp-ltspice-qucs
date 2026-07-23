"""GNSS-specific desense in check_coex_matrix (issue #15).

GNSS victims break the generic power-vs-sensitivity model: real desense
from a co-located TX is dominated by broadband PA noise at the GNSS
frequency, and the industry metric is ΔC/N₀ (dB-Hz), not a blocking
margin. The model (documented assumptions, all hand-checkable):

- Interference noise density at the RX:
  ``I₀ = PA_noise_dBm/Hz − filter_rejection_at_victim − antenna_iso``
- GNSS effective noise floor: ``N₀ = −174 + NF`` dBm/Hz
- ``ΔC/N₀ = 10·log₁₀(1 + 10^((I₀−N₀)/10))`` — the effective-floor rise;
  at I₀ = N₀ this is exactly 3.010 dB.
- A fundamental/harmonic landing *inside* the GNSS band is CW-like:
  the correlator spreads it over the code rate,
  ``I₀ = J_dBm − 10·log₁₀(chip_rate)`` (Q = 1 assumed, documented).
- ``desense_margin_db`` for GNSS entries = 1 dB C/N₀ budget − ΔC/N₀,
  keeping the matrix sortable alongside generic entries.
"""

from __future__ import annotations

import math

import pytest

from mcp_rf_analysis.coex import check_coex_matrix

GPS_L1 = {
    "name": "GPS L1",
    "f_center_hz": 1.57542e9,
    "bandwidth_hz": 2.046e6,
    "victim_type": "gnss",
    "sensitivity_dbm": -160.0,
    "noise_figure_db": 2.0,
}


def _matrix(tx, rx, **kw):
    return check_coex_matrix([tx], [rx], **kw)["matrix"]


# ---------------------------------------------------------------------------
# Broadband-noise mechanism (the dominant real-world path)
# ---------------------------------------------------------------------------


def test_broadband_noise_hand_pin_negligible() -> None:
    """PA noise −155 dBm/Hz, 20 dB filter rejection at L1, 30 dB isolation:
    I₀ = −205; N₀ = −172; ΔC/N₀ = 10log10(1+10^−3.3) = 0.00218 dB."""
    tx = {
        "name": "TX900",
        "f_center_hz": 915e6,
        "power_dbm": 30.0,
        "broadband_noise_dbm_hz": -155.0,
    }
    rx = {**GPS_L1, "filter_rejection_db": 20.0}
    rows = [r for r in _matrix(tx, rx, antenna_iso_db=30.0) if r["mechanism"] == "broadband_noise"]
    assert len(rows) == 1
    r = rows[0]
    assert r["i0_dbm_hz"] == pytest.approx(-205.0)
    assert r["gnss_noise_floor_dbm_hz"] == pytest.approx(-172.0)
    assert r["delta_cn0_db_hz"] == pytest.approx(10 * math.log10(1 + 10**-3.3), rel=1e-9)
    assert r["concern"] == "none"


def test_broadband_noise_equal_densities_is_3db() -> None:
    """I₀ = N₀ exactly → ΔC/N₀ = 10log10(2) = 3.0103 dB, high concern."""
    tx = {"name": "TX", "f_center_hz": 915e6, "power_dbm": 30.0, "broadband_noise_dbm_hz": -147.0}
    rx = dict(GPS_L1)  # no filter rejection
    rows = [r for r in _matrix(tx, rx, antenna_iso_db=25.0) if r["mechanism"] == "broadband_noise"]
    assert rows[0]["i0_dbm_hz"] == pytest.approx(-172.0)
    assert rows[0]["delta_cn0_db_hz"] == pytest.approx(10 * math.log10(2.0), rel=1e-9)
    assert rows[0]["concern"] in ("high", "critical")


def test_broadband_noise_strong_case() -> None:
    """I₀ − N₀ = +17 dB → ΔC/N₀ = 17.086 dB, critical."""
    tx = {"name": "TX", "f_center_hz": 915e6, "power_dbm": 30.0, "broadband_noise_dbm_hz": -130.0}
    rows = [
        r
        for r in _matrix(tx, dict(GPS_L1), antenna_iso_db=25.0)
        if r["mechanism"] == "broadband_noise"
    ]
    assert rows[0]["delta_cn0_db_hz"] == pytest.approx(10 * math.log10(1 + 10**1.7), rel=1e-9)
    assert rows[0]["concern"] == "critical"


def test_victim_side_pa_noise_override_wins() -> None:
    """The issue's sketch puts pa_broadband_noise_dbm_hz_at_offset on the
    victim (they know their offset) — it must override the TX default."""
    tx = {"name": "TX", "f_center_hz": 915e6, "power_dbm": 30.0, "broadband_noise_dbm_hz": -130.0}
    rx = {**GPS_L1, "pa_broadband_noise_dbm_hz_at_offset": -160.0}
    rows = [r for r in _matrix(tx, rx, antenna_iso_db=25.0) if r["mechanism"] == "broadband_noise"]
    assert rows[0]["i0_dbm_hz"] == pytest.approx(-185.0)


# ---------------------------------------------------------------------------
# In-band CW landings (harmonic / fundamental)
# ---------------------------------------------------------------------------


def test_harmonic_landing_in_l1_uses_chip_rate_spreading() -> None:
    """TX at 525.14 MHz: 3H = 1575.42 MHz dead-centre in L1. 30 dBm with
    −40 dBc → J = −10 − 25(iso) = −35 dBm; I₀ = −35 − 10log10(1.023e6)
    = −95.10 dBm/Hz."""
    tx = {
        "name": "TX525",
        "f_center_hz": 525.14e6,
        "power_dbm": 30.0,
        "filtered_harmonic_dbc": {"3H": -40.0},
    }
    rows = [
        r for r in _matrix(tx, dict(GPS_L1), antenna_iso_db=25.0) if r["mechanism"] == "harmonic_3"
    ]
    assert len(rows) == 1
    r = rows[0]
    assert r["i0_dbm_hz"] == pytest.approx(-35.0 - 10 * math.log10(1.023e6), rel=1e-9)
    assert r["delta_cn0_db_hz"] > 50.0
    assert r["concern"] == "critical"
    assert "chip_rate_hz" in r["assumptions"]


def test_harmonic_outside_gnss_band_not_reported_as_cw() -> None:
    """915 MHz TX: no integer harmonic lands in L1, so the only GNSS
    mechanism is broadband noise."""
    tx = {"name": "TX", "f_center_hz": 915e6, "power_dbm": 30.0}
    mechs = {r["mechanism"] for r in _matrix(tx, dict(GPS_L1), antenna_iso_db=25.0)}
    assert mechs == {"broadband_noise"}


# ---------------------------------------------------------------------------
# Matrix integration
# ---------------------------------------------------------------------------


def test_gnss_margin_keeps_matrix_sortable_with_generic_victims() -> None:
    """GNSS entries carry desense_margin_db = 1 dB budget − ΔC/N₀ so mixed
    matrices sort worst-first without special cases."""
    tx = {"name": "TX", "f_center_hz": 915e6, "power_dbm": 30.0, "broadband_noise_dbm_hz": -130.0}
    lte = {
        "name": "LTE B8 RX",
        "f_center_hz": 942.5e6,
        "bandwidth_hz": 35e6,
        "sensitivity_dbm": -97.0,
    }
    out = check_coex_matrix([tx], [dict(GPS_L1), lte], antenna_iso_db=25.0)
    matrix = out["matrix"]
    assert all("desense_margin_db" in r for r in matrix)
    margins = [r["desense_margin_db"] for r in matrix]
    assert margins == sorted(margins), "worst-first ordering must hold for mixed entries"
    gnss_rows = [r for r in matrix if r["victim"] == "GPS L1"]
    for r in gnss_rows:
        assert r["desense_margin_db"] == pytest.approx(1.0 - r["delta_cn0_db_hz"], rel=1e-9)


def test_generic_victims_are_unchanged() -> None:
    """No victim_type → the pre-#15 generic model, byte-for-byte fields."""
    tx = {"name": "TX", "f_center_hz": 915e6, "power_dbm": 30.0}
    lte = {
        "name": "LTE B8 RX",
        "f_center_hz": 915e6,
        "bandwidth_hz": 35e6,
        "sensitivity_dbm": -97.0,
    }
    rows = _matrix(tx, lte, antenna_iso_db=25.0)
    assert rows and all("delta_cn0_db_hz" not in r for r in rows)
    assert rows[0]["mechanism"] == "fundamental"


def test_gnss_entries_document_assumptions() -> None:
    tx = {"name": "TX", "f_center_hz": 915e6, "power_dbm": 30.0}
    rows = [
        r
        for r in _matrix(tx, dict(GPS_L1), antenna_iso_db=25.0)
        if r["mechanism"] == "broadband_noise"
    ]
    a = rows[0]["assumptions"]
    assert a["noise_figure_db"] == 2.0
    assert a["pa_broadband_noise_dbm_hz"] == -150.0  # documented default
    assert "cn0_budget_db" in a
