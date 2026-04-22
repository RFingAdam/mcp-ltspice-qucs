"""Network-level S-parameter operations: cascade, deembed, renormalize,
stability, Smith chart data."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import skrf as rf
from numpy.typing import NDArray

from rf_mcp_common.touchstone import read_touchstone, write_touchstone


def cascade_networks(s2p_paths: list[str | Path], output_path: str | Path) -> Path:
    """Cascade two or more 2-port networks (left-to-right order)."""
    if len(s2p_paths) < 2:
        raise ValueError("Need at least 2 networks to cascade")
    nets = [read_touchstone(p) for p in s2p_paths]
    # Resample all networks to the common (intersection) frequency grid
    f_common = nets[0].f
    for n in nets[1:]:
        f_common = np.intersect1d(f_common, n.f)
    if f_common.size < 2:
        # Fall back to the first network's freq grid; interpolate the others
        f_common = nets[0].f
    nets = [n.interpolate(rf.Frequency.from_f(f_common, unit="Hz")) for n in nets]
    cascaded = nets[0]
    for n in nets[1:]:
        cascaded = cascaded**n
    return write_touchstone(cascaded, output_path)


def deembed_network(
    measured_s2p: str | Path,
    fixture_left_s2p: str | Path,
    output_path: str | Path,
    fixture_right_s2p: str | Path | None = None,
) -> Path:
    """De-embed fixtures from a measured network: ``measured = L · DUT · R``.

    If ``fixture_right_s2p`` is omitted, both fixtures are assumed
    identical to ``fixture_left_s2p`` (symmetric calibration).
    """
    meas = read_touchstone(measured_s2p)
    left = read_touchstone(fixture_left_s2p)
    right = read_touchstone(fixture_right_s2p) if fixture_right_s2p else left.copy()

    f_common = np.intersect1d(meas.f, left.f)
    f_common = np.intersect1d(f_common, right.f)
    if f_common.size < 2:
        raise ValueError("Frequency grids of measurement and fixtures don't overlap")
    fr = rf.Frequency.from_f(f_common, unit="Hz")
    meas = meas.interpolate(fr)
    left = left.interpolate(fr)
    right = right.interpolate(fr)

    dut = left.inv**meas**right.inv
    return write_touchstone(dut, output_path)


def renormalize_impedance(s2p_path: str | Path, new_z0: float, output_path: str | Path) -> Path:
    """Renormalize an S-parameter file to a new reference impedance."""
    net = read_touchstone(s2p_path)
    net.renormalize(new_z0)
    return write_touchstone(net, output_path)


def compute_stability(s2p_path: str | Path) -> dict[str, list[float]]:
    """Return Rollett K-factor and |Δ| across frequency."""
    net = read_touchstone(s2p_path)
    s = net.s
    s11, s12, s21, s22 = s[:, 0, 0], s[:, 0, 1], s[:, 1, 0], s[:, 1, 1]
    delta = s11 * s22 - s12 * s21
    k = (1 - np.abs(s11) ** 2 - np.abs(s22) ** 2 + np.abs(delta) ** 2) / (
        2 * np.abs(s12 * s21) + 1e-30
    )
    mu = (1 - np.abs(s11) ** 2) / (np.abs(s22 - np.conj(s11) * delta) + np.abs(s12 * s21) + 1e-30)
    return {
        "freq_hz": net.f.tolist(),
        "k_factor": k.real.tolist(),
        "delta_mag": np.abs(delta).tolist(),
        "mu_factor": mu.real.tolist(),
        "unconditionally_stable": bool(np.all((k > 1) & (np.abs(delta) < 1))),
    }


def smith_chart_data(s2p_path: str | Path, port: int = 1) -> dict[str, list[float]]:
    """Return real and imaginary parts of S_{ii} for plotting on a Smith chart."""
    net = read_touchstone(s2p_path)
    if port < 1 or port > net.nports:
        raise ValueError(f"port must be in [1, {net.nports}], got {port}")
    sii = net.s[:, port - 1, port - 1]
    z_norm = (1 + sii) / (1 - sii + 1e-30)
    return {
        "freq_hz": net.f.tolist(),
        "s_real": sii.real.tolist(),
        "s_imag": sii.imag.tolist(),
        "z_norm_real": z_norm.real.tolist(),
        "z_norm_imag": z_norm.imag.tolist(),
        "z0": float(net.z0[0, 0].real),
    }


def s21_db_at(net: rf.Network, freq_hz: float) -> float:
    """Linearly-interpolated |S21| in dB at a single frequency."""
    s21 = np.interp(freq_hz, net.f, net.s[:, 1, 0])
    return float(20.0 * np.log10(max(abs(s21), 1e-12)))


def s21_db_array(net: rf.Network, freq_hz: NDArray[np.float64]) -> NDArray[np.float64]:
    s21 = np.interp(freq_hz, net.f, np.abs(net.s[:, 1, 0]))
    return 20.0 * np.log10(np.maximum(s21, 1e-12))
