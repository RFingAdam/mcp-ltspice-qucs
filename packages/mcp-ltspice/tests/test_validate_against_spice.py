"""Tests for validate_against_spice.

When ngspice is not available in the environment, the tool should fall
back gracefully to ``verdict='spice_unavailable'`` rather than failing.
When ngspice IS available, we should see a sensible verdict (typically
``'agree'`` for an ideal LC ladder where analytical and SPICE math line
up almost exactly).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_ltspice.asc_io import generate_lpf_asc
from mcp_ltspice.synthesis import Topology, synthesize_lc_lpf
from mcp_ltspice.validate_spice import validate_against_spice


def test_butterworth_5th_order_validates(tmp_path: Path):
    """Synthesize a 5th-order Butterworth at fc=1 GHz and reconcile."""
    design = synthesize_lc_lpf(
        "butterworth",
        order=5,
        cutoff_hz=1.0e9,
        z0=50.0,
        topology=Topology.SERIES_FIRST,
    )
    asc_path = tmp_path / "butter5.asc"
    generate_lpf_asc(
        design.components,
        asc_path,
        topology="lpf_t_butterworth_chebyshev",
        z0=50.0,
        f_start_hz=1e6,
        f_stop_hz=5e9,
    )

    spec = {
        "passband": {
            "f_start": 100e6,
            "f_stop": 800e6,
            "il_max_db": 1.0,
            "rl_min_db": 14.0,
        },
        "stopband_targets": [],
    }

    result = validate_against_spice(
        asc_path,
        design.components,
        spec=spec,
        passband_threshold_db=0.5,
        stopband_threshold_db=3.0,
    )

    # Either ngspice ran and we get a verdict, or it's unavailable.
    assert result["verdict"] in {"agree", "minor_disagreement", "disagree", "spice_unavailable"}

    if result["verdict"] != "spice_unavailable":
        # When SPICE is available, an ideal LC ladder should agree with
        # the analytical S-parameter calculation to high precision.
        assert result["max_delta_passband_db"] < 1.5
        assert Path(result["spice_s2p_path"]).is_file()
        assert Path(result["analytical_s2p_path"]).is_file()


def test_missing_asc_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        validate_against_spice(
            tmp_path / "nonexistent.asc",
            components={"L1": 6.8e-9},
        )


def test_returns_envelope_shape(tmp_path: Path):
    """Output dict should have the documented schema regardless of SPICE availability."""
    design = synthesize_lc_lpf(
        "butterworth",
        order=3,
        cutoff_hz=1.0e9,
        z0=50.0,
        topology=Topology.SERIES_FIRST,
    )
    asc_path = tmp_path / "butter3.asc"
    generate_lpf_asc(design.components, asc_path)

    result = validate_against_spice(asc_path, design.components)

    expected_keys = {"verdict", "spice_s2p_path", "analytical_s2p_path"}
    assert expected_keys.issubset(result.keys())
