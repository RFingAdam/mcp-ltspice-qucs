"""Tests for skrf-wrapping network operations."""

from __future__ import annotations

import numpy as np
import pytest

from mcp_rf_analysis.network_ops import (
    cascade_networks,
    compute_stability,
    deembed_network,
    renormalize_impedance,
    smith_chart_data,
)
from rf_mcp_common.touchstone import read_touchstone


def test_cascade_thru_with_lpf_is_lpf(thru_s2p, lpf_s2p, tmp_path) -> None:
    out = cascade_networks([thru_s2p, lpf_s2p], tmp_path / "cascade.s2p")
    cascaded = read_touchstone(out)
    lpf = read_touchstone(lpf_s2p)
    # |S21| should be very close to original LPF (within numerical noise)
    np.testing.assert_allclose(np.abs(cascaded.s[:, 1, 0]), np.abs(lpf.s[:, 1, 0]), atol=1e-6)


def test_cascade_requires_at_least_two(tmp_path, thru_s2p) -> None:
    with pytest.raises(ValueError):
        cascade_networks([thru_s2p], tmp_path / "out.s2p")


def test_deembed_recovers_dut(lpf_s2p, thru_s2p, tmp_path) -> None:
    # If "fixture" is a thru, deembedding it should leave the DUT alone
    out = deembed_network(lpf_s2p, thru_s2p, tmp_path / "dut.s2p", thru_s2p)
    dut = read_touchstone(out)
    lpf = read_touchstone(lpf_s2p)
    np.testing.assert_allclose(np.abs(dut.s[:, 1, 0]), np.abs(lpf.s[:, 1, 0]), atol=1e-6)


def test_renormalize_changes_z0(lpf_s2p, tmp_path) -> None:
    out = renormalize_impedance(lpf_s2p, 75.0, tmp_path / "z75.s2p")
    net = read_touchstone(out)
    assert float(net.z0[0, 0].real) == pytest.approx(75.0)


def test_compute_stability_returns_k_factor(lpf_s2p) -> None:
    res = compute_stability(lpf_s2p)
    assert "k_factor" in res
    assert "delta_mag" in res
    assert "mu_factor" in res
    # A passive lossless filter sits at the K=1 stability boundary in its
    # passband — verify the K-factor array exists and is real-valued.
    import numpy as np

    k = np.asarray(res["k_factor"])
    assert k.size == len(res["freq_hz"])
    assert np.isfinite(k).all()


def test_smith_chart_data_returns_z_norm(lpf_s2p) -> None:
    data = smith_chart_data(lpf_s2p, port=1)
    assert "z_norm_real" in data
    assert "z_norm_imag" in data
    assert len(data["freq_hz"]) == len(data["s_real"])


def test_smith_chart_invalid_port_raises(lpf_s2p) -> None:
    with pytest.raises(ValueError):
        smith_chart_data(lpf_s2p, port=99)
