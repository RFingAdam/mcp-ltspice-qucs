"""Tests for restricted-band-aware transmission-zero placement."""

from __future__ import annotations

import pytest

from mcp_ltspice.coex_zeros import (
    DEFAULT_SEVERITY,
    place_zeros_for_coex,
)


# Worldwide HaLow passband (covers EU 863-870, US/AU/NZ 902-928, JP 916.5-927.5)
HALOW_WORLDWIDE = (863e6, 928e6)

# Common LTE victims that overlap HaLow harmonics
LTE_B3_DL = {"name": "LTE B3 DL", "freq_range_hz": [1805e6, 1880e6], "category": "lte_dl"}
LTE_B7_DL = {"name": "LTE B7 DL", "freq_range_hz": [2620e6, 2690e6], "category": "lte_dl"}
LTE_B41 = {"name": "LTE B41", "freq_range_hz": [2496e6, 2690e6], "category": "lte_dl"}
LTE_B9_DL = {"name": "LTE B9 DL", "freq_range_hz": [1844.9e6, 1879.9e6], "category": "lte_dl"}
LTE_B38 = {"name": "LTE B38", "freq_range_hz": [2570e6, 2620e6], "category": "lte_dl"}


class TestHaLowWorldwide:
    """Worldwide HaLow LPF — the canonical use case for this tool."""

    def test_two_zeros_for_2h_3h(self):
        """2H zero protects LTE B3, 3H zero protects LTE B7/B41."""
        result = place_zeros_for_coex(
            HALOW_WORLDWIDE,
            harmonics=[2, 3],
            victim_bands=[LTE_B3_DL, LTE_B7_DL, LTE_B41, LTE_B9_DL, LTE_B38],
        )
        zeros = result["zeros"]
        assert len(zeros) == 2

        # 2H zero should land near 1830 MHz (centre of B3 DL overlap)
        # B3 overlap with 1726-1856 = 1805-1856; centre ≈ 1830.5
        z2 = next(z for z in zeros if z["harmonic"] == 2)
        assert 1820e6 <= z2["target_freq_hz"] <= 1850e6, (
            f"2H zero at {z2['target_freq_hz']/1e6:.1f} MHz outside expected range 1820–1850"
        )

        # 3H zero should land in LTE B7/B41 overlap (2620–2690)
        z3 = next(z for z in zeros if z["harmonic"] == 3)
        assert 2600e6 <= z3["target_freq_hz"] <= 2700e6, (
            f"3H zero at {z3['target_freq_hz']/1e6:.1f} MHz outside expected range 2600–2700"
        )

        # Trap-index hints should be ascending with frequency
        assert z2["trap_index_hint"] < z3["trap_index_hint"]
        assert z2["trap_index_hint"] == 2  # first trap = L2/C2
        assert z3["trap_index_hint"] == 4  # second trap = L4/C4

    def test_b3_dl_covered_by_2h_zero(self):
        """The chosen 2H zero must list LTE B3 DL among its covered victims."""
        result = place_zeros_for_coex(
            HALOW_WORLDWIDE, [2, 3], [LTE_B3_DL, LTE_B7_DL]
        )
        z2 = next(z for z in result["zeros"] if z["harmonic"] == 2)
        names = [v["name"] for v in z2["victims_covered"]]
        assert "LTE B3 DL" in names

    def test_no_unprotected_victims_when_all_covered(self):
        """When n_zeros = num harmonics and all victims fall in some harmonic,
        no victim should be flagged unprotected."""
        result = place_zeros_for_coex(
            HALOW_WORLDWIDE,
            [2, 3],
            [LTE_B3_DL, LTE_B7_DL, LTE_B41],
            n_zeros=2,
        )
        assert result["unprotected_victims"] == []


class TestEdgeCases:
    """Edge cases and degenerate inputs."""

    def test_no_victims_returns_centroid_zeros(self):
        """With no victims, falls back to centre of harmonic landing."""
        result = place_zeros_for_coex(HALOW_WORLDWIDE, [2, 3], victim_bands=[])
        zeros = result["zeros"]
        assert len(zeros) == 2
        # 2H landing centroid: (2*863 + 2*928) / 2 = 1791 MHz
        z2 = next(z for z in zeros if z["harmonic"] == 2)
        assert abs(z2["target_freq_hz"] - 1791e6) < 1e6

    def test_invalid_passband_raises(self):
        with pytest.raises(ValueError, match="f_high.*must be"):
            place_zeros_for_coex((928e6, 863e6), [2, 3], victim_bands=[])

    def test_empty_harmonics_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            place_zeros_for_coex(HALOW_WORLDWIDE, [], victim_bands=[])

    def test_harmonic_below_2_raises(self):
        with pytest.raises(ValueError, match="≥ 2"):
            place_zeros_for_coex(HALOW_WORLDWIDE, [1, 2], victim_bands=[])

    def test_n_zeros_caps_output(self):
        """Asking for fewer zeros than harmonics keeps the highest-severity ones."""
        result = place_zeros_for_coex(
            HALOW_WORLDWIDE,
            [2, 3, 5],
            [LTE_B7_DL],  # only 3H overlaps this
            n_zeros=1,
        )
        # The 3H zero should win because it has actual victim coverage
        assert len(result["zeros"]) == 1
        assert result["zeros"][0]["harmonic"] == 3


class TestSeverityWeighting:
    """Severity-weighted centroid math."""

    def test_high_severity_pulls_centroid(self):
        """A high-severity victim pulls the TZ toward it."""
        gnss_at_low = {
            "name": "GNSS-edge",
            "freq_range_hz": [1726e6, 1750e6],
            "category": "gnss",
            "severity": 10.0,
        }
        lte_at_high = {
            "name": "LTE-edge",
            "freq_range_hz": [1830e6, 1856e6],
            "category": "lte_dl",
            "severity": 1.0,
        }
        result = place_zeros_for_coex(
            HALOW_WORLDWIDE, [2], [gnss_at_low, lte_at_high]
        )
        z = result["zeros"][0]
        # Higher-severity GNSS at 1738 should dominate over LTE at 1843.
        assert z["target_freq_hz"] < 1800e6, (
            f"High-severity GNSS should pull TZ toward 1738; got {z['target_freq_hz']/1e6:.1f}"
        )

    def test_default_severity_lookup(self):
        """Categories without explicit severity get default DEFAULT_SEVERITY values."""
        v = {"name": "X", "freq_range_hz": [2620e6, 2690e6], "category": "lte_dl"}
        result = place_zeros_for_coex(HALOW_WORLDWIDE, [3], [v])
        # The chosen zero record should reflect the default LTE_DL severity.
        z = result["zeros"][0]
        covered = z["victims_covered"][0]
        assert covered["severity"] == DEFAULT_SEVERITY["lte_dl"]


class TestUnprotectedVictims:
    """Reporting on victims that no chosen zero covers."""

    def test_uncovered_harmonic_listed(self):
        """If we cap n_zeros below the number of victim-bearing harmonics,
        the omitted ones surface in unprotected_victims."""
        result = place_zeros_for_coex(
            HALOW_WORLDWIDE,
            [2, 3],
            [LTE_B3_DL, LTE_B7_DL],
            n_zeros=1,
        )
        # Only one zero kept; the other harmonic's victim should show up.
        assert len(result["zeros"]) == 1
        assert len(result["unprotected_victims"]) >= 1


class TestVictimNormalisation:
    """Different ways to specify victim bands."""

    def test_freq_range_hz_form(self):
        v = {"name": "X", "freq_range_hz": [2620e6, 2690e6]}
        result = place_zeros_for_coex(HALOW_WORLDWIDE, [3], [v])
        assert result["zeros"][0]["target_freq_hz"] > 0

    def test_low_high_form(self):
        v = {"name": "X", "f_low_hz": 2620e6, "f_high_hz": 2690e6}
        result = place_zeros_for_coex(HALOW_WORLDWIDE, [3], [v])
        assert result["zeros"][0]["target_freq_hz"] > 0

    def test_center_bw_form(self):
        v = {"name": "X", "f_center_hz": 2655e6, "bandwidth_hz": 70e6}
        result = place_zeros_for_coex(HALOW_WORLDWIDE, [3], [v])
        assert result["zeros"][0]["target_freq_hz"] > 0

    def test_missing_freq_raises(self):
        with pytest.raises(ValueError, match="missing one of"):
            place_zeros_for_coex(
                HALOW_WORLDWIDE, [3], [{"name": "broken"}]
            )

    def test_inverted_freq_range_raises(self):
        with pytest.raises(ValueError, match="f_high < f_low"):
            place_zeros_for_coex(
                HALOW_WORLDWIDE, [3], [{"name": "X", "freq_range_hz": [3e9, 2e9]}]
            )


class TestRationale:
    """The markdown rationale must reflect chosen zeros and unprotected victims."""

    def test_rationale_mentions_chosen_zeros(self):
        result = place_zeros_for_coex(
            HALOW_WORLDWIDE, [2, 3], [LTE_B3_DL, LTE_B7_DL]
        )
        rationale = result["rationale"]
        assert "863.0–928.0 MHz" in rationale
        assert "2H" in rationale
        assert "3H" in rationale

    def test_rationale_lists_unprotected(self):
        result = place_zeros_for_coex(
            HALOW_WORLDWIDE, [2, 3], [LTE_B3_DL, LTE_B7_DL], n_zeros=1
        )
        # The 1-zero choice leaves one of the harmonics' victims out.
        if result["unprotected_victims"]:
            assert "UNPROTECTED" in result["rationale"]
