"""Pytest fixtures for mcp-rf-analysis tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from rf_mcp_common.touchstone import network_to_touchstone


@pytest.fixture
def lpf_s2p(tmp_path: Path) -> Path:
    """A simple 3rd-order Butterworth LPF .s2p with fc≈500 MHz, computed
    analytically (no simulator)."""
    from mcp_ltspice.extract import (  # noqa: PLC0415 — fixture-local import
        components_dict_to_elements,
        ladder_sparams_from_components,
    )
    from mcp_ltspice.synthesis import synthesize_lc_lpf  # noqa: PLC0415

    design = synthesize_lc_lpf("butterworth", order=3, cutoff_hz=500e6)
    f = np.geomspace(1e6, 5e9, 501)
    elements = components_dict_to_elements(design.components, topology="series_first")
    s = ladder_sparams_from_components(elements, f, z0=design.z0)
    return network_to_touchstone(f, s, tmp_path / "lpf3.s2p", z0=design.z0)


@pytest.fixture
def thru_s2p(tmp_path: Path) -> Path:
    """A pass-through (S21=1) network — useful for cascade identity tests.

    Uses the same frequency grid as ``lpf_s2p`` so cascade/deembed tests
    don't lose resolution to grid intersection.
    """
    f = np.geomspace(1e6, 5e9, 501)
    s = np.zeros((f.size, 2, 2), dtype=np.complex128)
    s[:, 0, 1] = 1.0
    s[:, 1, 0] = 1.0
    return network_to_touchstone(f, s, tmp_path / "thru.s2p", z0=50.0)
