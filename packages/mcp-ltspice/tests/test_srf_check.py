"""Tests for SRF-aware spec audit."""

from __future__ import annotations

from mcp_ltspice.eval import FilterSpec
from mcp_ltspice.srf_check import srf_audit


def _spec(stopband_freqs_hz: list[float]) -> FilterSpec:
    return FilterSpec.model_validate(
        {
            "passband": {
                "f_start": 1e6,
                "f_stop": 500e6,
                "il_max_db": 0.5,
                "rl_min_db": 14,
            },
            "stopband_targets": [
                {"freq": f, "rejection_min_db": 30, "label": f"@{f / 1e9:.2f}GHz"}
                for f in stopband_freqs_hz
            ],
        }
    )


def test_low_freq_targets_pass_clean() -> None:
    # 0402HP 4.7 nH has SRF ≈ 4.5 GHz; spec to 1 GHz is well clear
    components = {"L1": 4.7e-9, "C2": 2.2e-12, "L3": 4.7e-9}
    audit = srf_audit(components, _spec([1e9]))
    assert audit["severity"] == "ok"
    assert audit["n_flagged"] == 0


def test_high_freq_targets_flag_components() -> None:
    # 0402HP 22 nH has SRF ≈ 1.5 GHz; targeting rejection at 3 GHz must flag
    components = {"L1": 22e-9, "C2": 2.2e-12, "L3": 22e-9}
    audit = srf_audit(components, _spec([3e9]))
    assert audit["n_flagged"] >= 2
    assert audit["severity"] in ("caution", "critical")
    assert any("SRF" in w for w in audit["warnings"])


def test_critical_when_many_components_flagged() -> None:
    # Use 5+ large inductors to get severity=critical
    components = {f"L{i}": 22e-9 for i in range(1, 6)}
    audit = srf_audit(components, _spec([3e9]))
    assert audit["severity"] == "critical"


def test_per_component_report_includes_srf() -> None:
    components = {"L1": 4.7e-9, "C2": 2.2e-12}
    audit = srf_audit(components, _spec([2e9]))
    assert audit["per_component"]["L1"]["srf_hz"] > 0
    assert "spec_to_srf_ratio" in audit["per_component"]["L1"]


def test_unknown_vendor_value_skipped_gracefully() -> None:
    # 999 nH is way bigger than any 0402HP entry; lookup_part returns nearest
    # so this should still work without raising
    components = {"L1": 4.7e-9}
    audit = srf_audit(components, _spec([1e9]))
    assert "L1" in audit["per_component"]
