"""Tests for spec evaluation and template loading."""

from __future__ import annotations

from mcp_rf_analysis.spec_eval import (
    check_passband_compliance,
    check_rejection_at,
    evaluate_against_spec_template,
    list_spec_templates,
)


def test_check_rejection_pass(lpf_s2p) -> None:
    # 3rd-order Butterworth LPF at fc=500MHz; at 5 GHz rejection should be huge
    res = check_rejection_at(lpf_s2p, 5e9, min_rejection_db=30)
    assert res["status"] == "pass"
    assert res["margin_db"] > 0


def test_check_rejection_fail_in_passband(lpf_s2p) -> None:
    res = check_rejection_at(lpf_s2p, 100e6, min_rejection_db=30)
    assert res["status"] == "fail"


def test_check_rejection_out_of_sweep(lpf_s2p) -> None:
    res = check_rejection_at(lpf_s2p, 100e9, min_rejection_db=30)
    assert res["status"] == "out_of_sweep"


def test_check_passband_compliance_pass(lpf_s2p) -> None:
    res = check_passband_compliance(
        lpf_s2p,
        1e6,
        250e6,
        il_max_db=0.5,
        rl_min_db=15,
    )
    assert res["status"] == "pass"


def test_check_passband_compliance_fail_above_cutoff(lpf_s2p) -> None:
    # Forcing passband through 1 GHz where the filter is rolling off
    res = check_passband_compliance(
        lpf_s2p,
        1e6,
        1e9,
        il_max_db=0.5,
        rl_min_db=15,
    )
    assert res["status"] == "fail"


def test_list_spec_templates_finds_bundled() -> None:
    names = list_spec_templates()
    assert "halow_us_lpf" in names
    assert "fcc_part15_247_915mhz" in names


def test_evaluate_against_spec_template_runs(lpf_s2p) -> None:
    # Use the FCC 915 MHz template — fixture LPF doesn't pass it (cutoff
    # is 500 MHz so the 902-928 passband is in the stopband), but the
    # evaluator should run and report results coherently.
    result = evaluate_against_spec_template(lpf_s2p, "fcc_part15_247_915mhz")
    assert "criteria" in result
    assert "overall" in result
    assert all("status" in c for c in result["criteria"])
