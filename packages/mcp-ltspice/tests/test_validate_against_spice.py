"""Reconciliation of SPICE vs the analytical preview (issue #16).

The whole pipeline can run without ever calling SPICE — every ``.s2p`` can
come from the closed-form ladder. This tool exists so a reported yield or
margin can be backed by an actual simulation. The tests pin the three
outcomes that matter: a design whose SPICE and analytical responses agree,
one where the analytical model is deliberately wrong and they disagree, and
the graceful path when no simulator is installed.
"""

from __future__ import annotations

import shutil

import numpy as np
import pytest

from mcp_ltspice import validate as V
from mcp_ltspice.asc_io import generate_lpf_asc
from mcp_ltspice.synthesis.lc_filter import synthesize_lc_lpf
from mcp_ltspice.validate import (
    Verdict,
    analytical_network,
    result_to_payload,
    validate_against_spice,
)

HAS_NGSPICE = shutil.which("ngspice") is not None
requires_ngspice = pytest.mark.skipif(not HAS_NGSPICE, reason="ngspice not installed")


@pytest.fixture
def lpf(tmp_path):
    design = synthesize_lc_lpf("butterworth", order=3, cutoff_hz=1e9)
    asc = generate_lpf_asc(design.components, tmp_path / "lpf.asc")
    return design, asc


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_analytical_network_is_a_real_filter() -> None:
    design = synthesize_lc_lpf("butterworth", order=3, cutoff_hz=1e9)
    freq = np.logspace(8, 10, 201)
    net = analytical_network(design.components, freq)
    s21_db = 20.0 * np.log10(np.abs(net.s[:, 1, 0]))
    i_fc = int(np.argmin(np.abs(freq - 1e9)))
    assert s21_db[i_fc] == pytest.approx(-3.0, abs=0.2), "3 dB point should be at fc"
    assert s21_db[-1] < -30.0, "should reject well into the stopband"


def test_payload_is_json_friendly() -> None:
    result = V.ValidationResult(
        verdict=Verdict.AGREE,
        freq_hz=np.array([1e9, 2e9]),
        delta_s21_db=np.array([0.01, 0.4]),
        delta_phase_deg=np.array([0.1, 0.5]),
        max_delta_passband_db=0.01,
        max_delta_stopband_db=0.4,
        simulator="ngspice",
    )
    payload = result_to_payload(result, top_n_points=2)
    assert payload["verdict"] == "agree"
    assert len(payload["worst_points"]) == 2
    # Every value must be a plain float/str/list, not numpy.
    import json

    json.dumps(payload)


def test_spice_unavailable_returns_analytical_not_an_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(V, "detect_simulator", lambda: None)
    design = synthesize_lc_lpf("butterworth", order=3, cutoff_hz=1e9)
    asc = generate_lpf_asc(design.components, tmp_path / "lpf.asc")

    result = validate_against_spice(asc, design.components)
    assert result.verdict == Verdict.SPICE_UNAVAILABLE
    assert result.analytical_network is not None
    assert result.spice_network is None
    assert "No SPICE simulator" in (result.note or "")


# ---------------------------------------------------------------------------
# Against real ngspice
# ---------------------------------------------------------------------------


@requires_ngspice
@pytest.mark.ngspice
@pytest.mark.integration
def test_matching_design_agrees(lpf) -> None:
    design, asc = lpf
    result = validate_against_spice(asc, design.components, prefer="ngspice")
    assert result.verdict == Verdict.AGREE, (
        f"max Δpass={result.max_delta_passband_db:.4f} dB, flagged={result.flagged_regions}"
    )
    assert result.max_delta_passband_db < 0.5
    assert result.simulator == "ngspice"
    assert not result.flagged_regions


@requires_ngspice
@pytest.mark.ngspice
@pytest.mark.integration
def test_wrong_analytical_model_disagrees(lpf) -> None:
    """Feed the analytical side the wrong component values.

    SPICE simulates what the .asc actually draws; the analytical response is
    computed from doubled values, so they must diverge in the passband.
    """
    design, asc = lpf
    wrong = {k: v * 2.0 for k, v in design.components.items()}
    result = validate_against_spice(asc, wrong, prefer="ngspice")
    assert result.verdict == Verdict.DISAGREE
    assert result.max_delta_passband_db > 1.0
    assert result.flagged_regions, "a disagreement must flag at least one region"


@requires_ngspice
@pytest.mark.ngspice
@pytest.mark.integration
def test_delta_arrays_line_up_with_the_grid(lpf) -> None:
    design, asc = lpf
    result = validate_against_spice(asc, design.components, prefer="ngspice")
    assert result.freq_hz is not None
    assert result.delta_s21_db is not None
    assert result.delta_phase_deg is not None
    n = result.freq_hz.size
    assert result.delta_s21_db.shape == (n,)
    assert result.delta_phase_deg.shape == (n,)


@requires_ngspice
@pytest.mark.ngspice
@pytest.mark.integration
def test_mcp_tool_writes_both_touchstone_files(tmp_path, lpf) -> None:
    import mcp_ltspice.server as S

    design, asc = lpf
    fn = getattr(S.validate_against_spice, "fn", S.validate_against_spice)
    env = fn(
        str(asc),
        design.components,
        prefer="ngspice",
        output_spice_s2p=str(tmp_path / "spice.s2p"),
        output_analytical_s2p=str(tmp_path / "analytical.s2p"),
    ).model_dump()

    assert env["status"] == "ok", env["error"]
    assert env["data"]["verdict"] == "agree"
    assert (tmp_path / "spice.s2p").is_file()
    assert (tmp_path / "analytical.s2p").is_file()
    assert "worst_points" in env["data"]


@requires_ngspice
@pytest.mark.ngspice
@pytest.mark.integration
def test_thresholds_are_configurable(lpf) -> None:
    """An absurdly tight passband threshold turns agreement into a flag."""
    design, asc = lpf
    strict = validate_against_spice(
        asc, design.components, prefer="ngspice", passband_threshold_db=1e-6
    )
    assert strict.verdict != Verdict.AGREE
    assert strict.flagged_regions
