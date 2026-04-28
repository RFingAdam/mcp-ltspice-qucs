"""Tests for the new `mode: Literal[...]` parameter on place_transmission_zero
and the deprecation of `preserve_ratio: bool`.

Background: prior `trap_lc_for_freq(preserve_ratio=False)` with both
l_existing and c_existing provided silently fell through none of the
branches and substituted L=1 nH. The mode-based API fixes that and also
replaces the ambiguous Boolean with explicit semantics.
"""

from __future__ import annotations

import math
import warnings

import pytest

from mcp_ltspice.synthesis.zeros import (
    place_transmission_zero,
    set_trap_frequency,
    trap_lc_for_freq,
)


def _f_resonance(L: float, C: float) -> float:
    return 1.0 / (2.0 * math.pi * math.sqrt(L * C))


# A 5th-order elliptic-style components dict with two trap pairs.
COMPONENTS = {
    "L1": 4.0e-9,
    "L2": 2.0e-9,
    "C2": 8.0e-12,  # original trap at ~1.26 GHz
    "L3": 6.0e-9,
    "L4": 1.5e-9,
    "C4": 4.0e-12,  # original trap at ~2.05 GHz
    "L5": 4.0e-9,
}


class TestTrapLcForFreqModes:
    def test_preserve_ratio_scales_both(self):
        L, C = trap_lc_for_freq(
            target_freq_hz=2e9,
            l_existing=2e-9,
            c_existing=8e-12,
            mode="preserve_ratio",
        )
        # Achieved resonance at target
        assert math.isclose(_f_resonance(L, C), 2e9, rel_tol=1e-6)
        # L/C ratio preserved
        assert math.isclose(L / C, 2e-9 / 8e-12, rel_tol=1e-6)

    def test_hold_l_keeps_l(self):
        L, C = trap_lc_for_freq(
            target_freq_hz=2e9,
            l_existing=2e-9,
            c_existing=8e-12,
            mode="hold_l",
        )
        assert L == pytest.approx(2e-9, rel=1e-9)
        assert math.isclose(_f_resonance(L, C), 2e9, rel_tol=1e-6)

    def test_hold_c_keeps_c(self):
        L, C = trap_lc_for_freq(
            target_freq_hz=2e9,
            l_existing=2e-9,
            c_existing=8e-12,
            mode="hold_c",
        )
        assert C == pytest.approx(8e-12, rel=1e-9)
        assert math.isclose(_f_resonance(L, C), 2e9, rel_tol=1e-6)

    def test_default_mode_is_preserve_ratio(self):
        L1, C1 = trap_lc_for_freq(2e9, l_existing=2e-9, c_existing=8e-12)
        L2, C2 = trap_lc_for_freq(
            2e9, l_existing=2e-9, c_existing=8e-12, mode="preserve_ratio"
        )
        assert (L1, C1) == (L2, C2)

    def test_invalid_freq_raises(self):
        with pytest.raises(ValueError, match=">0"):
            trap_lc_for_freq(0, l_existing=1e-9, c_existing=1e-12, mode="hold_l")

    def test_preserve_ratio_requires_both(self):
        with pytest.raises(ValueError, match="requires both"):
            trap_lc_for_freq(2e9, l_existing=1e-9, mode="preserve_ratio")

    def test_hold_l_requires_l(self):
        with pytest.raises(ValueError, match="requires l_existing"):
            trap_lc_for_freq(2e9, c_existing=1e-12, mode="hold_l")

    def test_hold_c_requires_c(self):
        with pytest.raises(ValueError, match="requires c_existing"):
            trap_lc_for_freq(2e9, l_existing=1e-9, mode="hold_c")


class TestFallThroughBugFixed:
    """Regression: previously, preserve_ratio=False with both L and C
    provided fell through every branch and silently substituted L=1nH.
    Now the deprecation shim maps it to mode='hold_c' (a defined
    behaviour) and emits a warning."""

    def test_preserve_ratio_false_with_both_no_longer_falls_through(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            L, C = trap_lc_for_freq(
                2e9,
                l_existing=2e-9,
                c_existing=8e-12,
                preserve_ratio=False,
            )
        # The shim maps preserve_ratio=False → mode='hold_c'.
        # So C is held and L is recomputed.
        assert C == pytest.approx(8e-12, rel=1e-9)
        # NOT L=1nH (the buggy fall-through default)
        assert L != pytest.approx(1e-9, rel=1e-3)
        # Achieved resonance is at target
        assert math.isclose(_f_resonance(L, C), 2e9, rel_tol=1e-6)
        # And a deprecation warning was emitted
        assert any(
            issubclass(rec.category, DeprecationWarning) for rec in w
        )


class TestDeprecationShim:
    def test_preserve_ratio_true_maps_to_preserve_ratio_mode(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            L_legacy, C_legacy = trap_lc_for_freq(
                2e9,
                l_existing=2e-9,
                c_existing=8e-12,
                preserve_ratio=True,
            )
        L_new, C_new = trap_lc_for_freq(
            2e9,
            l_existing=2e-9,
            c_existing=8e-12,
            mode="preserve_ratio",
        )
        assert math.isclose(L_legacy, L_new, rel_tol=1e-12)
        assert math.isclose(C_legacy, C_new, rel_tol=1e-12)
        assert any(
            issubclass(rec.category, DeprecationWarning) for rec in w
        )

    def test_both_mode_and_preserve_ratio_warns_mode_wins(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            L, C = trap_lc_for_freq(
                2e9,
                l_existing=2e-9,
                c_existing=8e-12,
                mode="hold_c",
                preserve_ratio=True,  # ignored
            )
        # mode='hold_c' wins → C is preserved
        assert C == pytest.approx(8e-12, rel=1e-9)
        assert any(
            issubclass(rec.category, DeprecationWarning) for rec in w
        )


class TestSetTrapFrequency:
    def test_modifies_only_target_trap(self):
        new = set_trap_frequency(
            COMPONENTS, trap_index=2, target_freq_hz=1.8e9,
            mode="preserve_ratio", snap_series=None,
        )
        # Only L2 + C2 changed
        assert new["L1"] == COMPONENTS["L1"]
        assert new["L3"] == COMPONENTS["L3"]
        assert new["L4"] == COMPONENTS["L4"]
        assert new["C4"] == COMPONENTS["C4"]
        assert new["L5"] == COMPONENTS["L5"]
        # L2/C2 do change
        assert new["L2"] != COMPONENTS["L2"]
        assert new["C2"] != COMPONENTS["C2"]

    def test_missing_trap_raises(self):
        with pytest.raises(KeyError):
            set_trap_frequency(
                COMPONENTS, trap_index=10, target_freq_hz=2e9, mode="preserve_ratio"
            )


class TestPlaceTransmissionZero:
    def test_returns_diagnostic_info(self):
        result = place_transmission_zero(
            COMPONENTS, trap_index=2, target_freq_hz=1.8e9,
            mode="preserve_ratio", snap_series=None,
        )
        assert result["target_freq_hz"] == 1.8e9
        assert "achieved_freq_hz" in result
        assert math.isclose(
            result["achieved_freq_hz"], 1.8e9, rel_tol=1e-6
        )
        assert "previous" in result
        assert result["previous"]["L2"] == COMPONENTS["L2"]
        assert result["previous"]["C2"] == COMPONENTS["C2"]

    def test_negative_trap_index_raises(self):
        with pytest.raises(ValueError, match="positive"):
            place_transmission_zero(
                COMPONENTS, trap_index=-1, target_freq_hz=2e9, mode="preserve_ratio"
            )

    def test_legacy_preserve_ratio_still_works(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = place_transmission_zero(
                COMPONENTS, trap_index=2, target_freq_hz=1.8e9,
                preserve_ratio=True, snap_series=None,
            )
        assert math.isclose(
            result["achieved_freq_hz"], 1.8e9, rel_tol=1e-6
        )
        assert any(
            issubclass(rec.category, DeprecationWarning) for rec in w
        )
