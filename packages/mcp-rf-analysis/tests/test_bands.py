"""Tests for the bundled band/limit databases."""

from __future__ import annotations

import pytest

from mcp_rf_analysis.bands import (
    is_in_restricted_band,
    list_5gnr_bands,
    list_fcc_restricted_bands,
    list_gnss_bands,
    list_halow_channels,
    list_ism_bands,
    list_lte_bands,
    lookup_band_by_freq,
)


def test_lte_band_3_present_with_correct_uplink() -> None:
    bands = list_lte_bands()
    b3 = next(b for b in bands if b["band"] == 3)
    assert b3["f_ul"] == [1710e6, 1785e6]
    assert b3["f_dl"] == [1805e6, 1880e6]


def test_lte_filter_by_region() -> None:
    americas = list_lte_bands(region="Americas")
    assert all("Americas" in b["region"] for b in americas)
    assert any(b["band"] == 25 for b in americas)


def test_5gnr_fr1_n78_in_3p5ghz() -> None:
    bands = list_5gnr_bands("fr1")
    n78 = next(b for b in bands if b["band"] == "n78")
    assert n78["f_ul"][0] == 3300e6
    assert n78["f_dl"][1] == 3800e6


def test_5gnr_fr2_present() -> None:
    bands = list_5gnr_bands("fr2")
    assert any(b["band"] == "n257" for b in bands)


def test_5gnr_unknown_family_raises() -> None:
    with pytest.raises(ValueError):
        list_5gnr_bands("fr99")


def test_gnss_l1_at_1575_42_mhz() -> None:
    sigs = list_gnss_bands("GPS")
    l1 = next(s for s in sigs if "L1" in s["signal"])
    assert l1["f_center"] == 1575.42e6


def test_ism_915_in_region_2_only() -> None:
    region2 = list_ism_bands(region=2)
    assert any(b["label"] == "915 MHz" for b in region2)
    region1 = list_ism_bands(region=1)
    assert not any(b["label"] == "915 MHz" for b in region1)


def test_halow_us_channels_in_902_928() -> None:
    us = list_halow_channels("US")
    assert us["band"] == [902e6, 928e6]
    for ch in us["channels_1mhz_centers"]:
        assert 902e6 <= ch <= 928e6


def test_halow_unknown_region_raises() -> None:
    with pytest.raises(ValueError):
        list_halow_channels("ZZ")


def test_lookup_band_by_freq_finds_lte_b25_uplink() -> None:
    result = lookup_band_by_freq(1880e6)
    assert any(b["band"] == 25 for b in result["lte_ul"])


def test_lookup_band_by_freq_finds_gps_l1() -> None:
    result = lookup_band_by_freq(1575.42e6)
    assert any("L1" in s["signal"] for s in result["gnss"])


def test_lookup_band_by_freq_finds_ism_24g() -> None:
    result = lookup_band_by_freq(2450e6)
    assert any(b["label"] == "2.4 GHz" for b in result["ism"])


def test_fcc_restricted_band_check_at_gps_l1() -> None:
    is_in, info = is_in_restricted_band(1575.42e6)
    assert is_in
    assert info is not None
    assert "GPS" in info["notes"] or "telemetry" in info["notes"]


def test_fcc_restricted_band_check_outside() -> None:
    is_in, info = is_in_restricted_band(915e6)  # 915 MHz ISM is allowed
    assert not is_in
    assert info is None


def test_fcc_restricted_bands_listing_nonempty() -> None:
    rb = list_fcc_restricted_bands()
    assert len(rb) > 10
