"""Tests for the matplotlib renderer."""

from __future__ import annotations

import numpy as np

from mcp_ltspice.extract import (
    components_dict_to_elements,
    ladder_sparams_from_components,
)
from mcp_ltspice.render import render_response
from mcp_ltspice.synthesis import synthesize_lc_lpf
from rf_mcp_common.touchstone import network_to_touchstone


def test_render_writes_png(tmp_path) -> None:
    design = synthesize_lc_lpf("butterworth", order=5, cutoff_hz=1e9)
    f = np.geomspace(1e6, 5e9, 501)
    elements = components_dict_to_elements(design.components, topology="series_first")
    s = ladder_sparams_from_components(elements, f, z0=design.z0)
    s2p = network_to_touchstone(f, s, tmp_path / "design.s2p", z0=design.z0)

    out = render_response(
        s2p,
        tmp_path / "plot.png",
        markers=[(500e6, "passband edge"), (2e9, "2× fc")],
        title="Butterworth N=5 demo",
    )
    assert out.exists()
    assert out.stat().st_size > 1000  # non-trivial PNG


def test_render_respects_freq_range(tmp_path) -> None:
    design = synthesize_lc_lpf("butterworth", order=3, cutoff_hz=1e9)
    f = np.geomspace(1e6, 5e9, 501)
    elements = components_dict_to_elements(design.components, topology="series_first")
    s = ladder_sparams_from_components(elements, f, z0=design.z0)
    s2p = network_to_touchstone(f, s, tmp_path / "design.s2p", z0=design.z0)

    out = render_response(
        s2p,
        tmp_path / "plot.png",
        freq_range=(100e6, 2e9),
        show_s11=False,
    )
    assert out.exists()


def test_render_no_markers_no_s11(tmp_path) -> None:
    design = synthesize_lc_lpf("butterworth", order=3, cutoff_hz=1e9)
    f = np.geomspace(1e6, 5e9, 201)
    elements = components_dict_to_elements(design.components, topology="series_first")
    s = ladder_sparams_from_components(elements, f, z0=design.z0)
    s2p = network_to_touchstone(f, s, tmp_path / "design.s2p", z0=design.z0)

    out = render_response(s2p, tmp_path / "plot.png", show_s11=False)
    assert out.exists()
