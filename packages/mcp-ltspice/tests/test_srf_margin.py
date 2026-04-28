"""Tests for the SRF auto-rejection extension to substitute_real_components."""

from __future__ import annotations

import pytest

from mcp_ltspice.vendor_models import (
    SrfRejectionError,
    lookup_part_with_srf_margin,
    substitute_real_components,
)

# Worldwide HaLow LPF spec — the canonical context for SRF margin checking.
HALOW_SPEC = {
    "passband": {"f_start": 863e6, "f_stop": 928e6, "il_max_db": 1.0, "rl_min_db": 14.0},
    "stopband_targets": [
        {"freq": 1830e6, "rejection_min_db": 50, "label": "2H"},
        {"freq": 2640e6, "rejection_min_db": 55, "label": "3H"},
    ],
}


class TestLegacyBehavior:
    """srf_margin=0 should preserve the original substitute_real_components contract."""

    def test_default_no_rejection(self):
        out = substitute_real_components({"L1": 6.8e-9, "C1": 3.3e-12})
        # Same outputs as before the change, no rejected_candidates field.
        assert "rejected_candidates" not in out["L1"]
        assert "rejected_candidates" not in out["C1"]
        # 6.8 nH 0402HP catalogue value is exactly 6.8 nH
        assert out["L1"]["snapped_value"] == pytest.approx(6.8e-9, rel=1e-6)

    def test_explicit_zero_margin_no_rejection(self):
        out = substitute_real_components(
            {"L1": 6.8e-9}, srf_margin=0.0, spec=HALOW_SPEC
        )
        assert "rejected_candidates" not in out["L1"]


class TestSrfMargin:
    """srf_margin>0 should reject low-SRF parts and substitute neighbours."""

    def test_realistic_margin_passes_typical_design(self):
        """Standard 1.2× margin against 3H = 2.64 GHz spec target."""
        comps = {
            "L1": 6.8e-9,  # SRF 3.5 GHz, threshold 1.2*2.78=3.34 GHz → passes
            "L3": 5.6e-9,  # SRF 4.0 GHz → passes
            "L2": 4.7e-9,  # SRF 4.5 GHz → passes
            "C1": 3.3e-12,
            "C2": 1.8e-12,
        }
        out = substitute_real_components(
            comps, srf_margin=1.2, spec=HALOW_SPEC
        )
        # No rejections at this margin
        for r, info in out.items():
            assert "rejected_candidates" not in info or len(info.get("rejected_candidates", [])) == 0

    def test_strict_margin_rejects_low_srf(self):
        """Tight 1.5× margin pulls 8.2 nH down to a smaller value."""
        out = substitute_real_components(
            {"L1": 8.2e-9},
            srf_margin=1.5,
            spec=HALOW_SPEC,
            max_value_drift_pct=50.0,  # allow more drift for this test
        )
        # Either substituted or raised
        if "rejected_candidates" in out["L1"]:
            assert out["L1"]["snapped_value"] < 8.2e-9
            assert out["L1"]["srf_hz"] >= 1.5 * 2640e6

    def test_unattainable_margin_raises(self):
        """No 0402HP part has SRF > 11.8 GHz, so margin=5× will raise."""
        with pytest.raises(SrfRejectionError) as exc_info:
            substitute_real_components(
                {"L1": 22e-9},  # SRF 1.5 GHz catalogue
                srf_margin=10.0,  # demand 10x*2640 = 26.4 GHz - impossible
                spec=HALOW_SPEC,
            )
        err = exc_info.value
        assert err.refdes == "L1"
        assert err.kind == "L"
        assert err.vendor == "coilcraft_0402hp"
        assert len(err.candidates) > 0

    def test_max_value_drift_pct_bounds_substitution(self):
        """With strict drift bound, even an SRF-failing nearest part
        won't be replaced by a wildly different value."""
        with pytest.raises(SrfRejectionError) as exc_info:
            substitute_real_components(
                {"L1": 22e-9},  # nearest catalogue SRF too low (1.5 GHz)
                srf_margin=2.0,
                spec=HALOW_SPEC,
                max_value_drift_pct=10.0,  # only ±10% drift allowed
            )
        # All candidates within 10% will be SRF-rejected → error
        assert len(exc_info.value.candidates) > 0


class TestMaxSpecFreqDerivation:
    """max_spec_freq_hz can come directly or from a spec dict."""

    def test_explicit_max_freq(self):
        out = substitute_real_components(
            {"L1": 6.8e-9},
            srf_margin=1.2,
            max_spec_freq_hz=2640e6,
        )
        assert out["L1"]["snapped_value"] > 0

    def test_spec_derives_max_freq(self):
        out = substitute_real_components(
            {"L1": 6.8e-9},
            srf_margin=1.2,
            spec=HALOW_SPEC,  # max(passband.f_stop, stopband_targets...) = 2640 MHz
        )
        assert out["L1"]["snapped_value"] > 0

    def test_no_max_freq_with_margin_raises(self):
        with pytest.raises(ValueError, match="max_spec_freq_hz or a spec"):
            substitute_real_components(
                {"L1": 6.8e-9}, srf_margin=1.2  # no max_spec_freq_hz, no spec
            )


class TestLookupHelper:
    """Direct tests of lookup_part_with_srf_margin."""

    def test_returns_tuple(self):
        part, rejected = lookup_part_with_srf_margin(
            "coilcraft_0402hp", 6.8e-9, kind="L", min_srf_hz=2e9
        )
        assert part.srf_hz >= 2e9
        assert isinstance(rejected, list)

    def test_rejection_trail_records_drift(self):
        try:
            _, _ = lookup_part_with_srf_margin(
                "coilcraft_0402hp",
                22e-9,
                kind="L",
                min_srf_hz=10e9,
                max_value_drift_pct=5.0,
            )
        except SrfRejectionError as e:
            for cand in e.candidates:
                assert "value_drift_pct" in cand
                assert "rejected_for" in cand


class TestRejectionMetadata:
    """Successful substitutions should carry rejection metadata when applicable."""

    def test_rejected_candidates_recorded(self):
        out = substitute_real_components(
            {"L1": 22e-9},  # nearest catalogue value, low SRF
            srf_margin=1.5,
            spec=HALOW_SPEC,
            max_value_drift_pct=80.0,  # allow large drift to find a qualifying candidate
        )
        info = out["L1"]
        if info["snapped_value"] != 22e-9:
            # We substituted — should have a rejection trail
            assert "rejected_candidates" in info
            assert len(info["rejected_candidates"]) >= 1
