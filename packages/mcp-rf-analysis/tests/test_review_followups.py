"""Follow-up regressions: HaLow EU e.r.p., coex pair/filter handling, 15.247(d) spurious."""

from __future__ import annotations

import json
import math
from importlib import resources

import pytest

from mcp_rf_analysis.bands import list_halow_channels
from mcp_rf_analysis.coex import check_coex_matrix

# ---------------------------------------------------------------------------
# (a) HaLow EU limit is e.r.p.
# ---------------------------------------------------------------------------

#: Pinned to the same values as mcp-emc-regulations
#: data/wifi_standards.json bands.sub_1ghz.regions.etsi (max_erp_dbm = 14),
#: from ERC/REC 70-03 (edition of 9 October 2026) Annex 1 h1.3/h1.4 and
#: Annex 3 a1: 25 mW e.r.p. in 863-868 MHz. EIRP = ERP + 2.15 dB.
EU_MAX_ERP_DBM = 14
EU_MAX_ERP_MW = 25
EU_MAX_EIRP_DBM = 16.15


def _limits(name: str) -> dict:
    path = resources.files("mcp_rf_analysis").joinpath(f"resources/limits/{name}.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_halow_eu_limit_is_erp_with_derived_eirp() -> None:
    eu = list_halow_channels("EU")

    assert eu["max_erp_dbm"] == EU_MAX_ERP_DBM
    assert eu["max_erp_mw"] == EU_MAX_ERP_MW
    assert eu["max_eirp_dbm"] == pytest.approx(EU_MAX_EIRP_DBM)
    assert eu["max_eirp_dbm"] == pytest.approx(eu["max_erp_dbm"] + 2.15)
    assert 10 * math.log10(EU_MAX_ERP_MW) == pytest.approx(EU_MAX_ERP_DBM, abs=0.03)
    cite = eu["power_limit_citation"]
    for token in ("ERC/REC 70-03", "h1.3", "h1.4", "Annex 3 band a1", "2.8%", "LBT+AFA"):
        assert token in cite, token
    assert "mcp-emc-regulations" in eu["source_of_truth"]


def test_halow_regions_without_primary_citation_are_flagged_unverified() -> None:
    us = list_halow_channels("US")
    assert us["power_limit_status"] == "cited"
    assert "15.247(b)(3)" in us["power_limit_citation"]
    assert us["max_eirp_dbm"] == 36 and us["max_conducted_power_dbm"] == 30

    for region in ("KR", "CN", "SG", "AU_NZ", "IN"):
        entry = list_halow_channels(region)
        assert entry["power_limit_status"] == "UNVERIFIED", region
        assert entry["power_limit_note"].startswith("UNVERIFIED"), region


#: Pinned to the same values as mcp-emc-regulations
#: data/wifi_standards.json bands.sub_1ghz.regions.japan and
#: data/japan_rules.json arib_standards.std_t108.key_parameters, from the MIC
#: Ordinance Regulating Radio Equipment Art. 49-14 item 7 (ro)/(ha), checked
#: against the e-Gov text: gain <= 3 dBi, EIRP <= 16 dBm, 200 kHz unit
#: channels from 920.6 MHz (up to 20 with carrier sense), 920.5-928.1 MHz.
#: The antenna-power figure is in a separate MIC Notification: UNVERIFIED.
JP_MAX_EIRP_DBM = 16
JP_MAX_ANTENNA_GAIN_DBI = 3
JP_UNIT_CHANNEL_KHZ = 200
JP_EIRP_BAND_HZ = [920.5e6, 928.1e6]


def test_halow_jp_carries_verified_t108_eirp_cap() -> None:
    jp = list_halow_channels("JP")

    assert jp["power_limit_status"] == "cited"
    assert jp["max_eirp_dbm"] == JP_MAX_EIRP_DBM
    assert jp["max_antenna_gain_dbi"] == JP_MAX_ANTENNA_GAIN_DBI
    assert jp["unit_channel_khz"] == JP_UNIT_CHANNEL_KHZ
    assert jp["max_simultaneous_unit_channels"] == 20
    assert jp["eirp_band_hz"] == JP_EIRP_BAND_HZ
    assert "Art. 49-14 item 7 (ro)" in jp["power_limit_citation"]
    assert jp["power_limit_verification"].startswith("verified_against_primary")
    # same rule as emc-regulations: the antenna-power figure stays UNVERIFIED
    assert jp["max_antenna_power_status"] == "UNVERIFIED"
    assert jp["max_antenna_power_note"].startswith("UNVERIFIED")
    assert "mcp-emc-regulations" in jp["source_of_truth"]
    # the old unsupported 20 dBm figure is gone
    assert jp["max_eirp_dbm"] != 20


def test_etsi_868_sub_band_labels_match_erc_70_03() -> None:
    limits = _limits("etsi")["limits"]
    h17 = limits["en_300_220_h1p7"]
    assert (h17["f_low"], h17["f_high"], h17["max_erp_dbm"]) == (869.4e6, 869.65e6, 27)
    assert "h1.7" in h17["band_label"]
    h16 = limits["en_300_220_h1p6"]
    assert (h16["f_low"], h16["f_high"], h16["max_erp_dbm"]) == (868.7e6, 869.2e6, 14)
    assert "h1.6" in h16["band_label"]


# ---------------------------------------------------------------------------
# (b) check_coex_matrix: unordered pairs once, filters on the right path
# ---------------------------------------------------------------------------

#: Three radios, antenna isolation 20 dB everywhere.
#: SUB:  TX 915 MHz 23 dBm; PA 2H -30 dBc, 3H -40 dBc before its TX filter,
#:       TX filter rejects 2H by 40 dB, 3H by 45 dB. RX 902-928 MHz,
#:       blocking spec -40 dBm, RX filter rejects out-of-band by 50 dB.
#: LTE:  RX only, 1805-1880 MHz, noise floor -104 dBm, blocking spec -25 dBm,
#:       RX filter rejects out-of-band by 30 dB.
#: WLAN: TX 2437 MHz 20 dBm (no harmonics listed); RX 2400-2483.5 MHz,
#:       blocking spec -30 dBm, RX filter rejects out-of-band by 40 dB.
SUB_TX = {
    "name": "SUB",
    "f_center_hz": 915e6,
    "power_dbm": 23.0,
    "harmonic_dbc": {"2H": -30.0, "3H": -40.0},
    "tx_filter_rejection_db": {"2H": 40.0, "3H": 45.0},
}
WLAN_TX = {"name": "WLAN", "f_center_hz": 2437e6, "power_dbm": 20.0, "harmonic_dbc": {}}
SUB_RX = {
    "name": "SUB",
    "f_range_hz": [902e6, 928e6],
    "sensitivity_dbm": -100.0,
    "noise_floor_dbm": -110.0,
    "blocking_spec_dbm": -40.0,
    "rx_filter_rejection_db": 50.0,
}
LTE_RX = {
    "name": "LTE",
    "f_range_hz": [1805e6, 1880e6],
    "sensitivity_dbm": -97.0,
    "noise_floor_dbm": -104.0,
    "blocking_spec_dbm": -25.0,
    "rx_filter_rejection_db": 30.0,
}
WLAN_RX = {
    "name": "WLAN",
    "f_range_hz": [2400e6, 2483.5e6],
    "sensitivity_dbm": -90.0,
    "noise_floor_dbm": -95.0,
    "blocking_spec_dbm": -30.0,
    "rx_filter_rejection_db": 40.0,
}


def test_three_radio_matrix_hand_checked() -> None:
    res = check_coex_matrix([SUB_TX, WLAN_TX], [SUB_RX, LTE_RX, WLAN_RX], antenna_iso_db=20.0)

    # 3 radios -> 3 unordered pairs: {SUB,LTE}, {SUB,WLAN}, {LTE,WLAN}.
    # Directions: SUB->LTE, SUB->WLAN, WLAN->SUB, WLAN->LTE (LTE never transmits).
    assert res["n_pairs_analyzed"] == 3
    assert res["n_directions_analyzed"] == 4

    rows = {(r["aggressor"], r["victim"], r["mechanism"], r["coupling"]): r for r in res["matrix"]}
    assert len(res["matrix"]) == 5

    # SUB 2H = 1830 MHz lands in LTE (in-band). Emitted: 23 - 30 = -7 dBm.
    # TX filter 40 dB at 2H, isolation 20 dB, no RX-filter credit (in passband):
    # P_rx = -7 - 40 - 20 = -67 dBm; I/N = -67 - (-104) = 37 dB;
    # desense = 10log10(1 + 10^3.7) = 37.0009 dB; margin = 1 - 37.0009.
    h2 = rows[("SUB", "LTE", "harmonic_2", "in_band")]
    assert h2["f_emit_hz"] == pytest.approx(1830e6)
    assert h2["predicted_at_rx_dbm"] == pytest.approx(-67.0)
    assert h2["rx_filter_rejection_db"] == 0.0
    assert h2["desense_db"] == pytest.approx(10 * math.log10(1 + 10**3.7))
    assert h2["desense_margin_db"] == pytest.approx(1 - 10 * math.log10(1 + 10**3.7))

    # Blockers: fundamental, victim RX filter, no TX-filter credit.
    # SUB -> LTE:  23 - 30 - 20 = -27 dBm vs -25 spec -> margin 2 dB.
    # SUB -> WLAN: 23 - 40 - 20 = -37 dBm vs -30 spec -> margin 7 dB.
    # WLAN -> SUB: 20 - 50 - 20 = -50 dBm vs -40 spec -> margin 10 dB.
    # WLAN -> LTE: 20 - 30 - 20 = -30 dBm vs -25 spec -> margin 5 dB.
    for (agg, vic), (p_rx, margin) in {
        ("SUB", "LTE"): (-27.0, 2.0),
        ("SUB", "WLAN"): (-37.0, 7.0),
        ("WLAN", "SUB"): (-50.0, 10.0),
        ("WLAN", "LTE"): (-30.0, 5.0),
    }.items():
        row = rows[(agg, vic, "fundamental", "out_of_band_blocker")]
        assert row["predicted_at_rx_dbm"] == pytest.approx(p_rx), (agg, vic)
        assert row["desense_margin_db"] == pytest.approx(margin), (agg, vic)
        assert row["desense_db"] is None
        assert row["tx_filter_rejection_db"] == 0.0

    # Sorted worst first.
    margins = [r["desense_margin_db"] for r in res["matrix"]]
    assert margins == sorted(margins)
    assert res["matrix"][0]["mechanism"] == "harmonic_2"


def test_fundamental_landing_in_victim_band_gets_no_tx_filter_credit() -> None:
    """The aggressor's TX filter passes its own fundamental; the old code
    subtracted its rejection from an in-band fundamental."""
    tx = [
        {
            "name": "A",
            "f_center_hz": 1850e6,
            "power_dbm": 10.0,
            "filter_rejection_db": 40.0,
            "harmonic_dbc": {},
        }
    ]
    rx = [
        {
            "name": "B",
            "f_range_hz": [1805e6, 1880e6],
            "sensitivity_dbm": -97.0,
            "noise_floor_dbm": -104.0,
        }
    ]
    row = check_coex_matrix(tx, rx, antenna_iso_db=20.0)["matrix"][0]
    assert row["predicted_at_rx_dbm"] == pytest.approx(10.0 - 20.0)


def test_harmonic_gets_tx_filter_rejection() -> None:
    """...while its harmonics are attenuated by it (the old code gave 0 dB)."""
    tx = [
        {
            "name": "A",
            "f_center_hz": 915e6,
            "power_dbm": 23.0,
            "filter_rejection_db": 40.0,
            "harmonic_dbc": {"2H": -30.0},
        }
    ]
    rx = [
        {
            "name": "B",
            "f_range_hz": [1805e6, 1880e6],
            "sensitivity_dbm": -97.0,
            "noise_floor_dbm": -104.0,
        }
    ]
    row = check_coex_matrix(tx, rx, antenna_iso_db=20.0)["matrix"][0]
    assert row["predicted_at_rx_dbm"] == pytest.approx(23.0 - 30.0 - 40.0 - 20.0)


def test_blocker_without_spec_is_listed_not_guessed() -> None:
    res = check_coex_matrix([WLAN_TX], [{**LTE_RX, "blocking_spec_dbm": None}], antenna_iso_db=20.0)
    assert res["matrix"] == []
    assert res["not_evaluated"][0]["mechanism"] == "out_of_band_blocker"


# ---------------------------------------------------------------------------
# (c) 15.247(d) spurious: relative outside restricted bands, 15.209 inside
# ---------------------------------------------------------------------------


def test_fcc_json_has_no_flat_spurious_dbm() -> None:
    fcc = _limits("fcc")
    for band in fcc["limits"].values():
        assert not any(k.startswith("spurious_") and k != "spurious_rule" for k in band), band
    spur = fcc["spurious_emissions_15_247_d"]
    out = spur["outside_restricted_bands"]
    assert out["min_attenuation_db_peak_power_method"] == 20
    assert out["min_attenuation_db_average_power_method"] == 30
    assert out["measurement_bandwidth_hz"] == 100000
    above_960 = spur["in_restricted_bands"]["table_15_209_a"][-1]
    assert (above_960["limit_uv_m"], above_960["limit_dbuv_m"], above_960["distance_m"]) == (
        500,
        54.0,
        3,
    )


def test_spurious_limit_outside_restricted_band_is_relative() -> None:
    from mcp_rf_analysis.bands import fcc_15247_spurious_limit

    # 1830 MHz is not in a 15.205 band. In-band peak 20 dBm/100 kHz.
    peak = fcc_15247_spurious_limit(1830e6, inband_peak_dbm_100khz=20.0)
    assert peak["in_restricted_band"] is False
    assert peak["limit_dbm_100khz"] == pytest.approx(0.0)  # 20 dB below
    avg = fcc_15247_spurious_limit(1830e6, inband_peak_dbm_100khz=20.0, power_method="average")
    assert avg["limit_dbm_100khz"] == pytest.approx(-10.0)  # 30 dB below
    # Never required below 15.209(a): -30 - 20 = -50 < -41.23 floor.
    low = fcc_15247_spurious_limit(1830e6, inband_peak_dbm_100khz=-30.0)
    assert low["limit_dbm_100khz"] == pytest.approx(54 + 20 * math.log10(3) - 104.77)


def test_spurious_limit_in_restricted_band_is_15_209_field_strength() -> None:
    from mcp_rf_analysis.bands import fcc_15247_spurious_limit

    # 2745 MHz is in 15.205 band 2690-2900 MHz: 54 dBuV/m @ 3 m -> -41.23 dBm EIRP.
    r = fcc_15247_spurious_limit(2745e6, inband_peak_dbm_100khz=20.0)
    assert r["in_restricted_band"] is True
    assert r["limit_dbuv_m"] == 54.0 and r["distance_m"] == 3
    assert r["limit_eirp_dbm"] == pytest.approx(54 + 9.5424 - 104.77, abs=1e-3)
    # 120 MHz is in 108-121.94 MHz: 150 uV/m = 43.5 dBuV/m @ 3 m -> -51.73 dBm EIRP.
    r = fcc_15247_spurious_limit(120e6)
    assert r["limit_dbuv_m"] == 43.5
    assert r["limit_eirp_dbm"] == pytest.approx(43.5 + 9.5424 - 104.77, abs=1e-3)
