"""FCC framework for harmonics depends on the radiator class.

Regression for a design-review finding: lookup_harmonic_victims flagged
clock-oscillator harmonics as falling in FCC "restricted bands". 47 CFR
15.205 restricted bands apply to intentional radiators; an unintentional
radiator (clock, crystal, digital logic) is judged against the 15.109
limits. Also guards the 15.205(a) table itself, which was a 26-row subset
containing a band that is not in the regulation (90.254-90.384 MHz) and
missing 40 that are.
"""

from __future__ import annotations

import pytest

from mcp_rf_analysis import server
from mcp_rf_analysis.bands import is_in_restricted_band, list_fcc_restricted_bands
from mcp_rf_analysis.coex import lookup_harmonic_victims


def test_clock_harmonic_is_not_reported_as_restricted_band_issue() -> None:
    # 40 MHz crystal, 3rd harmonic = 120 MHz, inside 108-121.94 MHz.
    (h3,) = lookup_harmonic_victims(40e6, [3], source_type="unintentional")
    assert h3["frequency_in_15_205_table"] is True  # frequency fact
    assert h3["in_fcc_restricted"] is False  # but 15.205 does not apply
    assert "check 15.109 limit" in h3["fcc_rule"]


def test_intentional_radiator_harmonic_reports_restricted_band() -> None:
    # 2440 MHz Wi-Fi/BLE 2nd harmonic = 4880 MHz, inside 4.5-5.15 GHz.
    (h2,) = lookup_harmonic_victims(2440e6, [2], source_type="intentional")
    assert h2["in_fcc_restricted"] is True
    assert "15.205" in h2["fcc_rule"] and "15.209" in h2["fcc_rule"]
    assert h2["fcc_restricted_info"]["cfr_cell"] == "4.5-5.15 GHz"


def test_unspecified_source_does_not_assert_a_restriction() -> None:
    (h3,) = lookup_harmonic_victims(40e6, [3])
    assert h3["in_fcc_restricted"] is None
    assert h3["frequency_in_15_205_table"] is True
    assert "15.109" in h3["fcc_rule"]


def test_unknown_source_type_rejected() -> None:
    with pytest.raises(ValueError, match="available"):
        lookup_harmonic_victims(40e6, [3], source_type="clock")


def test_server_tool_passes_source_type() -> None:
    env = server.lookup_harmonic_victims(40e6, [3], source_type="unintentional")
    assert env.status == "ok"
    assert env.data[0]["in_fcc_restricted"] is False


def test_restricted_table_is_the_full_15_205a_table() -> None:
    bands = list_fcc_restricted_bands()
    assert len(bands) == 65
    assert all("cfr_cell" in b for b in bands)


@pytest.mark.parametrize(
    ("freq_hz", "expected"),
    [
        (90.3e6, False),  # FM broadcast; was wrongly listed as restricted
        (13.38e6, True),  # 13.36-13.41 MHz, was missing
        (74.0e6, True),  # 73-74.6 MHz, was missing
        (3.7e9, True),  # 3600-4400 MHz, was missing
        (5.4e9, True),  # 5.35-5.46 GHz, was missing
        (7.5e9, True),  # 7.25-7.75 GHz, was missing
        (2.45e9, False),  # ISM band, never restricted
    ],
)
def test_restricted_band_membership(freq_hz: float, expected: bool) -> None:
    assert is_in_restricted_band(freq_hz)[0] is expected
