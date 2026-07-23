"""place_zeros_for_coex (issue #12): restricted-band-aware transmission-
zero placement.

The optimal TZ for a harmonic landing is the severity-weighted centroid
of its victim-overlap intervals — not the geometric centre of the
landing. The reference pin is hand-computed: passband 850–950 MHz gives
a 2H landing of 1700–1900 MHz; victims [1805, 1880] MHz at severity 2
and [1710, 1780] MHz at severity 1 overlap fully, so

    TZ = (2·75·1842.5 + 1·70·1745) / (2·75 + 1·70) = 398525/220
       = 1811.477 MHz.

Trap-index hints follow mcp-ltspice's elliptic convention (the fitter
consumes sorted-ascending ω_z, so the lowest zero belongs to trap 2,
the next to trap 4, ...), letting the client feed the result straight
into ``place_transmission_zero``.

Lives in mcp-rf-analysis (not mcp-ltspice as the issue sketched)
because the band data and harmonic lookups are here and mcp-ltspice has
no runtime dependency on this package.
"""

from __future__ import annotations

import pytest

from mcp_rf_analysis.coex_zeros import place_zeros_for_coex

MHZ = 1e6

B3ISH = {"name": "LTE B3 DL-ish", "freq_range_hz": [1805 * MHZ, 1880 * MHZ], "severity": 2.0}
MIDBAND = {"name": "mid victim", "freq_range_hz": [1710 * MHZ, 1780 * MHZ], "severity": 1.0}


def _run(**kwargs):
    defaults = {
        "passband_hz": (850 * MHZ, 950 * MHZ),
        "harmonics": [2],
        "victim_bands": [B3ISH, MIDBAND],
        "include_gnss": False,
        "include_fcc_restricted": False,
    }
    defaults.update(kwargs)
    return place_zeros_for_coex(**defaults)


# ---------------------------------------------------------------------------
# Centroid math
# ---------------------------------------------------------------------------


def test_severity_weighted_centroid_hand_pin() -> None:
    result = _run()
    zeros = result["zeros"]
    assert len(zeros) == 1
    z = zeros[0]
    assert z["harmonic"] == 2
    assert z["target_freq_hz"] == pytest.approx(398525.0 / 220.0 * MHZ, rel=1e-12)
    covered = {v["name"] for v in z["victims_covered"]}
    assert covered == {"LTE B3 DL-ish", "mid victim"}


def test_severity_weighting_pulls_the_zero() -> None:
    """Doubling one victim's severity must move the centroid toward it."""
    base = _run()["zeros"][0]["target_freq_hz"]
    heavier = _run(
        victim_bands=[{**B3ISH, "severity": 10.0}, MIDBAND],
    )["zeros"][0]["target_freq_hz"]
    assert heavier > base
    assert 1805 * MHZ < heavier < 1880 * MHZ


def test_overlap_is_clipped_to_the_landing() -> None:
    """A victim straddling the landing edge only counts its overlap: with
    the 2H landing ending at 1900 MHz, a victim [1850, 2000] MHz
    contributes [1850, 1900] (mid 1875), not its own centre (1925)."""
    result = _run(victim_bands=[{"name": "straddler", "freq_range_hz": [1850 * MHZ, 2000 * MHZ]}])
    z = result["zeros"][0]
    assert z["target_freq_hz"] == pytest.approx(1875 * MHZ, rel=1e-12)


def test_victim_outside_all_landings_is_reported_not_at_risk() -> None:
    result = _run(
        victim_bands=[B3ISH, {"name": "far away", "freq_range_hz": [5.0e9, 5.1e9]}],
    )
    assert {v["name"] for v in result["victims_not_at_risk"]} == {"far away"}
    assert all(v["name"] != "far away" for v in result["unprotected_victims"])


# ---------------------------------------------------------------------------
# Harmonic ranking, zero budget, fallback
# ---------------------------------------------------------------------------


def test_n_zeros_budget_picks_highest_aggregate_harmonic() -> None:
    """3H landing [2550, 2850] gets a heavy victim; with n_zeros=1 the 3H
    zero must win over the light 2H one, and the 2H victims land in
    unprotected_victims."""
    heavy = {"name": "heavy 3H victim", "freq_range_hz": [2600 * MHZ, 2700 * MHZ], "severity": 5.0}
    light = {"name": "light 2H victim", "freq_range_hz": [1750 * MHZ, 1760 * MHZ], "severity": 1.0}
    result = _run(harmonics=[2, 3], victim_bands=[heavy, light], n_zeros=1)
    zeros = result["zeros"]
    assert len(zeros) == 1
    assert zeros[0]["harmonic"] == 3
    assert zeros[0]["target_freq_hz"] == pytest.approx(2650 * MHZ, rel=1e-12)
    assert {v["name"] for v in result["unprotected_victims"]} == {"light 2H victim"}


def test_spare_zeros_fall_back_to_landing_centres() -> None:
    """With no 3H victims but n_zeros=2, the spare zero goes to the 3H
    landing centre and says so."""
    result = _run(harmonics=[2, 3], n_zeros=2)
    zeros = {z["harmonic"]: z for z in result["zeros"]}
    assert set(zeros) == {2, 3}
    assert zeros[3]["target_freq_hz"] == pytest.approx((2550 + 2850) / 2 * MHZ, rel=1e-12)
    assert zeros[3]["victims_covered"] == []
    assert "centre" in zeros[3]["placement"] or "fallback" in zeros[3]["placement"]
    assert zeros[2]["placement"] == "severity_weighted_centroid"


def test_trap_index_hints_follow_ascending_zero_order() -> None:
    """mcp-ltspice's elliptic fitter consumes sorted-ascending ω_z: the
    lowest zero maps to trap 2, the next to trap 4."""
    result = _run(harmonics=[2, 3], n_zeros=2)
    by_freq = sorted(result["zeros"], key=lambda z: z["target_freq_hz"])
    assert [z["trap_index_hint"] for z in by_freq] == [2, 4]


# ---------------------------------------------------------------------------
# Auto-loaded bands
# ---------------------------------------------------------------------------


def test_gnss_auto_load_catches_l1_at_third_harmonic() -> None:
    """525 MHz TX: 3H landing [1560, 1590] MHz straddles GPS L1
    (1575.42 MHz) — include_gnss must find it with no user victims."""
    result = place_zeros_for_coex(
        passband_hz=(520 * MHZ, 530 * MHZ),
        harmonics=[3],
        victim_bands=[],
        include_gnss=True,
        include_fcc_restricted=False,
    )
    z = result["zeros"][0]
    names = " ".join(v["name"] for v in z["victims_covered"])
    assert "GPS" in names and "L1" in names
    assert abs(z["target_freq_hz"] - 1575.42 * MHZ) < 5 * MHZ


def test_fcc_restricted_auto_load_contributes_victims() -> None:
    """GPS L1 sits inside an FCC restricted band too — with only
    include_fcc_restricted the 3H zero still lands near it."""
    result = place_zeros_for_coex(
        passband_hz=(520 * MHZ, 530 * MHZ),
        harmonics=[3],
        victim_bands=[],
        include_gnss=False,
        include_fcc_restricted=True,
    )
    z = result["zeros"][0]
    assert z["victims_covered"], "restricted bands must register as victims"
    assert 1560 * MHZ < z["target_freq_hz"] < 1590 * MHZ


def test_rationale_is_markdown_with_the_decisions() -> None:
    result = _run(harmonics=[2, 3], n_zeros=2)
    rat = result["rationale"]
    assert "2H" in rat and "3H" in rat
    assert "1811.5" in rat or "1811.48" in rat


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"passband_hz": (950 * MHZ, 850 * MHZ)}, "passband"),
        ({"harmonics": []}, "harmonics"),
        ({"harmonics": [1]}, "harmonics"),
        (
            {"victim_bands": [{"name": "bad", "freq_range_hz": [2e9, 1e9]}]},
            "freq_range",
        ),
        ({"n_zeros": 0}, "n_zeros"),
    ],
)
def test_invalid_inputs_rejected(kwargs, match) -> None:
    with pytest.raises(ValueError, match=match):
        _run(**kwargs)


# ---------------------------------------------------------------------------
# MCP tool envelope
# ---------------------------------------------------------------------------


def test_place_zeros_for_coex_tool() -> None:
    from mcp_rf_analysis import server

    env = server.place_zeros_for_coex(
        passband_hz=[850e6, 950e6],
        harmonics=[2],
        victim_bands=[B3ISH, MIDBAND],
        include_gnss=False,
        include_fcc_restricted=False,
    )
    assert env.status == "ok"
    assert env.data["zeros"][0]["target_freq_hz"] == pytest.approx(1811.477e6, rel=1e-4)


def test_place_zeros_for_coex_tool_error_envelope() -> None:
    from mcp_rf_analysis import server

    env = server.place_zeros_for_coex(
        passband_hz=[950e6, 850e6],
        harmonics=[2],
        victim_bands=[],
    )
    assert env.status == "error"
    assert "passband" in env.error
