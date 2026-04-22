"""Tests for the Touchstone helpers."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from rf_mcp_common.touchstone import (
    network_to_touchstone,
    read_touchstone,
    sparams_at,
)


def _make_pass_through_sparams(npts: int = 11) -> tuple[np.ndarray, np.ndarray]:
    """Ideal lossless 50 Ω pass-through: S21=S12=1, S11=S22=0."""
    f = np.linspace(1e6, 1e9, npts)
    s = np.zeros((npts, 2, 2), dtype=np.complex128)
    s[:, 0, 1] = 1.0
    s[:, 1, 0] = 1.0
    return f, s


def test_round_trip_writes_and_reads(tmp_path: Path) -> None:
    f, s = _make_pass_through_sparams()
    out = network_to_touchstone(f, s, tmp_path / "passthrough.s2p")
    assert out.exists()
    assert out.suffix == ".s2p"

    net = read_touchstone(out)
    assert net.nports == 2
    np.testing.assert_allclose(net.f, f, rtol=1e-9)
    np.testing.assert_allclose(net.s, s, rtol=1e-9, atol=1e-12)


def test_sparams_at_interpolates(tmp_path: Path) -> None:
    f, s = _make_pass_through_sparams()
    out = network_to_touchstone(f, s, tmp_path / "p.s2p")
    net = read_touchstone(out)

    # Pick a frequency between samples; S21 should still be ~1
    s_mid = sparams_at(net, freq_hz=5.5e8, interp=True)
    assert abs(s_mid[1, 0]) == pytest.approx(1.0, rel=1e-6)


def test_sparams_at_rejects_out_of_band(tmp_path: Path) -> None:
    f, s = _make_pass_through_sparams()
    out = network_to_touchstone(f, s, tmp_path / "p.s2p")
    net = read_touchstone(out)
    with pytest.raises(ValueError):
        sparams_at(net, freq_hz=10e9)


def test_read_missing_file_errors() -> None:
    with pytest.raises(FileNotFoundError):
        read_touchstone("/nonexistent/never.s2p")


def test_shape_mismatch_rejected(tmp_path: Path) -> None:
    f = np.linspace(1e6, 1e9, 10)
    s_wrong = np.zeros((9, 2, 2), dtype=np.complex128)
    with pytest.raises(ValueError):
        network_to_touchstone(f, s_wrong, tmp_path / "bad.s2p")


def test_non_square_sparams_rejected(tmp_path: Path) -> None:
    f = np.linspace(1e6, 1e9, 5)
    s_rect = np.zeros((5, 2, 3), dtype=np.complex128)
    with pytest.raises(ValueError):
        network_to_touchstone(f, s_rect, tmp_path / "bad.s2p")
