"""GNSS victim filter path: the RX filter only rejects out-of-band energy.

An aggressor emission (harmonic, spur or PA broadband noise) that lands
inside the GNSS passband passes the GNSS receiver's own filter, so only the
aggressor's TX filter and the antenna isolation reduce it. The receiver's
filter rejection applies only to an out-of-band blocker (the aggressor
fundamental), which is compared with the receiver blocking spec.
"""

from __future__ import annotations

import math

import pytest

from mcp_rf_analysis.coex import check_coex_matrix

GPS_L1_WITH_SAW = {
    "name": "GPS L1",
    "f_center_hz": 1.57542e9,
    "bandwidth_hz": 2.046e6,
    "victim_type": "gnss",
    "sensitivity_dbm": -160.0,
    "noise_figure_db": 2.0,
    # GNSS SAW rejection at out-of-band frequencies: must NOT be applied in-band
    "filter_rejection_db": 40.0,
    "blocking_spec_dbm": -20.0,
}


def _rows(tx, rx, iso):
    return check_coex_matrix([tx], [rx], antenna_iso_db=iso)


def test_in_band_harmonic_not_reduced_by_gnss_rx_filter() -> None:
    """Hand check. Aggressor 525.14 MHz x 3 = 1575.42 MHz (L1 centre).
    PA 20 dBm, 3H = -40 dBc before the TX filter, TX filter 10 dB at 3H,
    isolation 20 dB:
      J  = 20 - 40 - 10 - 20 = -50 dBm   (no -40 dB GNSS SAW: it is in-band)
      I0 = J - 10 log10(1.023e6) = -50 - 60.0988 = -110.0988 dBm/Hz
    The old code also subtracted the 40 dB RX filter (J = -90 dBm)."""
    tx = {
        "name": "TX525",
        "f_center_hz": 525.14e6,
        "power_dbm": 20.0,
        "harmonic_dbc": {"3H": -40.0},
        "tx_filter_rejection_db": 10.0,
    }
    out = _rows(tx, GPS_L1_WITH_SAW, iso=20.0)
    rows = [r for r in out["matrix"] if r["mechanism"] == "harmonic_3"]
    assert len(rows) == 1
    r = rows[0]
    assert r["assumptions"]["cw_power_at_rx_dbm"] == pytest.approx(-50.0)
    assert r["i0_dbm_hz"] == pytest.approx(-50.0 - 10 * math.log10(1.023e6))
    assert r["i0_dbm_hz"] == pytest.approx(-110.0988, abs=1e-4)
    assert r["assumptions"]["rx_filter_rejection_db"] == 0.0


def test_broadband_pa_noise_not_reduced_by_gnss_rx_filter() -> None:
    """Hand check. PA noise -150 dBm/Hz at L1, no aggressor-side filter,
    isolation 20 dB: I0 = -150 - 0 - 20 = -170 dBm/Hz (old code: -210).
    N0 = -174 + 2 = -172; dC/N0 = 10 log10(1 + 10^(2/10)) = 4.1244 dB."""
    tx = {
        "name": "TX915",
        "f_center_hz": 915e6,
        "power_dbm": 30.0,
        "broadband_noise_dbm_hz": -150.0,
    }
    out = _rows(tx, GPS_L1_WITH_SAW, iso=20.0)
    r = next(r for r in out["matrix"] if r["mechanism"] == "broadband_noise")
    assert r["i0_dbm_hz"] == pytest.approx(-170.0)
    assert r["delta_cn0_db_hz"] == pytest.approx(10 * math.log10(1 + 10**0.2), rel=1e-9)
    assert r["delta_cn0_db_hz"] == pytest.approx(4.1244, abs=1e-4)
    assert r["concern"] == "critical"

    # A filter on the aggressor side at the GNSS frequency does reduce it.
    tx_filtered = {**tx, "noise_filter_rejection_db": 30.0}
    r2 = next(
        r
        for r in _rows(tx_filtered, GPS_L1_WITH_SAW, iso=20.0)["matrix"]
        if r["mechanism"] == "broadband_noise"
    )
    assert r2["i0_dbm_hz"] == pytest.approx(-200.0)

    # Per-victim value: the aggressor filter's rejection at this victim's
    # own frequency overrides the transmitter-wide figure.
    victim = {**GPS_L1_WITH_SAW, "aggressor_filter_rejection_db": 25.0}
    r3 = next(
        r
        for r in _rows(tx_filtered, victim, iso=20.0)["matrix"]
        if r["mechanism"] == "broadband_noise"
    )
    assert r3["i0_dbm_hz"] == pytest.approx(-195.0)
    assert r3["assumptions"]["tx_noise_filter_rejection_db"] == pytest.approx(25.0)


def test_out_of_band_fundamental_is_a_blocker_with_gnss_rx_filter() -> None:
    """Hand check. Fundamental 915 MHz at 30 dBm is outside L1, so it is a
    blocker: at the LNA = 30 - 20 (isolation) - 40 (GNSS SAW) = -30 dBm,
    blocking spec -20 dBm -> margin +10 dB. The old code skipped it."""
    tx = {"name": "TX915", "f_center_hz": 915e6, "power_dbm": 30.0, "harmonic_dbc": {}}
    out = _rows(tx, GPS_L1_WITH_SAW, iso=20.0)
    rows = [r for r in out["matrix"] if r.get("coupling") == "out_of_band_blocker"]
    assert len(rows) == 1
    r = rows[0]
    assert r["rx_filter_rejection_db"] == pytest.approx(40.0)
    assert r["predicted_at_rx_dbm"] == pytest.approx(-30.0)
    assert r["desense_margin_db"] == pytest.approx(10.0)
    assert r["desense_db"] is None
