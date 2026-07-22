"""Regulatory band + channel database lookups.

All band JSON files are bundled under ``resources/bands/`` and loaded
lazily on first access. Frequencies in returned data are always Hz.
"""

from __future__ import annotations

import functools
import json
from importlib import resources
from typing import Any


@functools.cache
def _load_band_json(name: str) -> dict[str, Any]:
    """Load a JSON file from the package resources/bands/ directory."""
    pkg_resources = resources.files("mcp_rf_analysis").joinpath("resources/bands")
    path = pkg_resources.joinpath(f"{name}.json")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@functools.cache
def _load_limit_json(name: str) -> dict[str, Any]:
    pkg_resources = resources.files("mcp_rf_analysis").joinpath("resources/limits")
    path = pkg_resources.joinpath(f"{name}.json")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def list_lte_bands(region: str | None = None) -> list[dict[str, Any]]:
    """Return LTE bands. ``region`` filters by substring match on the
    band's ``region`` field (e.g. 'Americas', 'EMEA', 'Japan')."""
    data = _load_band_json("lte")["bands"]
    if region is None:
        return data
    needle = region.lower()
    matches = [b for b in data if needle in b.get("region", "").lower()]
    if not matches:
        # Don't hand back an empty list: "no LTE bands in EMEA" and
        # "you misspelled EMEA" are indistinguishable to the caller, and
        # only one of them is true. list_5gnr_bands / list_halow_channels
        # already raise on an unknown filter; match them.
        available = sorted({b.get("region", "") for b in data if b.get("region")})
        raise ValueError(f"No LTE bands match region {region!r}; available: {available}")
    return matches


def list_5gnr_bands(family: str = "fr1") -> list[dict[str, Any]]:
    """Return 5G NR bands; ``family`` is 'fr1' or 'fr2'."""
    data = _load_band_json("5gnr")
    key = f"bands_{family.lower()}"
    if key not in data:
        raise ValueError(f"Unknown 5G NR family: {family}")
    return data[key]


def list_gnss_bands(system: str | None = None) -> list[dict[str, Any]]:
    """Return GNSS signals. ``system`` filters by 'GPS', 'GLONASS',
    'Galileo', 'BeiDou'."""
    data = _load_band_json("gnss")["bands"]
    if system is None:
        return data
    needle = system.lower()
    matches = [b for b in data if b.get("system", "").lower() == needle]
    if not matches:
        available = sorted({b.get("system", "") for b in data if b.get("system")})
        raise ValueError(f"Unknown GNSS system {system!r}; available: {available}")
    return matches


def list_ism_bands(region: int | None = None) -> list[dict[str, Any]]:
    """Return ISM band allocations. ``region`` is 1, 2, or 3 (ITU)."""
    data = _load_band_json("ism")["bands"]
    if region is None:
        return data
    matches = [b for b in data if region in b.get("regions", [])]
    if not matches:
        available = sorted({r for b in data for r in b.get("regions", [])})
        raise ValueError(f"Unknown ITU region {region!r}; available: {available}")
    return matches


def list_halow_channels(region: str = "US") -> dict[str, Any]:
    """Return HaLow channel set for a region (e.g. 'US', 'EU', 'JP')."""
    data = _load_band_json("halow")["regions"]
    if region not in data:
        raise ValueError(f"Unknown HaLow region: {region}; available: {sorted(data)}")
    return data[region]


def lookup_band_by_freq(freq_hz: float) -> dict[str, list[dict[str, Any]]]:
    """Find all known bands containing ``freq_hz``.

    Returns a dict with keys 'lte_ul', 'lte_dl', '5gnr_ul', '5gnr_dl',
    'gnss', 'ism', 'halow' — each mapping to a list of matching entries.
    """
    matches: dict[str, list[dict[str, Any]]] = {
        "lte_ul": [],
        "lte_dl": [],
        "5gnr_ul": [],
        "5gnr_dl": [],
        "gnss": [],
        "ism": [],
        "halow": [],
    }
    for b in list_lte_bands():
        if b["f_ul"] and b["f_ul"][0] <= freq_hz <= b["f_ul"][1]:
            matches["lte_ul"].append(b)
        if b["f_dl"] and b["f_dl"][0] <= freq_hz <= b["f_dl"][1]:
            matches["lte_dl"].append(b)
    for family in ("fr1", "fr2"):
        for b in list_5gnr_bands(family):
            if b["f_ul"] and b["f_ul"][0] <= freq_hz <= b["f_ul"][1]:
                matches["5gnr_ul"].append({"family": family, **b})
            if b["f_dl"] and b["f_dl"][0] <= freq_hz <= b["f_dl"][1]:
                matches["5gnr_dl"].append({"family": family, **b})
    for b in list_gnss_bands():
        bw = b["bandwidth"] / 2
        if abs(freq_hz - b["f_center"]) <= bw:
            matches["gnss"].append(b)
    for b in list_ism_bands():
        if b["f_low"] <= freq_hz <= b["f_high"]:
            matches["ism"].append(b)
    for region, info in _load_band_json("halow")["regions"].items():
        if info["band"][0] <= freq_hz <= info["band"][1]:
            matches["halow"].append({"region": region, **info})
    return matches


def list_fcc_restricted_bands() -> list[dict[str, Any]]:
    """FCC §15.205 restricted bands (no fundamental emission allowed)."""
    return _load_limit_json("fcc")["restricted_bands"]


def is_in_restricted_band(freq_hz: float) -> tuple[bool, dict[str, Any] | None]:
    """Check if ``freq_hz`` falls into an FCC restricted band."""
    for r in list_fcc_restricted_bands():
        if r["f_low"] <= freq_hz <= r["f_high"]:
            return True, r
    return False, None
