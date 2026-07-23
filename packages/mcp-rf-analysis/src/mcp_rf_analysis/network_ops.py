"""Network-level S-parameter operations: cascade, deembed, renormalize,
stability, Smith chart data."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import cast

import numpy as np
import skrf as rf
from numpy.typing import NDArray

from rf_mcp_common.touchstone import read_touchstone, write_touchstone


def common_frequency_grid(
    nets: Sequence[rf.Network],
    labels: Sequence[str],
    *,
    collect_warnings: list[str] | None = None,
) -> NDArray[np.float64]:
    """Frequency grid shared by every network — the *overlap*, never beyond.

    One policy, used by both :func:`cascade_networks` and
    :func:`deembed_network`, which previously disagreed:

    - The grid spans ``[max(all f_min), min(all f_max)]``. Every output point
      therefore lies inside every input's measured range, so resampling is
      always interpolation and never extrapolation.
    - Disjoint ranges raise, naming each network's span. There is no
      meaningful answer to cascade a 1–2 GHz part with a 5–6 GHz one.
    - If the overlap is narrower than any input, that is reported through
      ``collect_warnings`` so callers can surface it rather than silently
      returning a narrower file than the user handed in.

    The old code intersected the grids with :func:`numpy.intersect1d`, which
    demands exact float equality. Two instruments essentially never produce
    bit-identical grids, so real inputs fell through to "use network 1's grid
    and interpolate the rest" — extrapolating the others past the end of
    their measured data, with no warning.
    """
    lo = max(float(n.f.min()) for n in nets)
    hi = min(float(n.f.max()) for n in nets)

    if lo >= hi:
        spans = ", ".join(
            f"{label}: {n.f.min() / 1e9:.6g}-{n.f.max() / 1e9:.6g} GHz"
            for label, n in zip(labels, nets, strict=True)
        )
        raise ValueError(
            f"Frequency ranges do not overlap, so there is nothing to compute "
            f"without extrapolating past measured data. Ranges — {spans}."
        )

    # Sample on the densest input's points inside the overlap, so the result
    # keeps the best resolution any input actually provides.
    best: NDArray[np.float64] | None = None
    for n in nets:
        inside = n.f[(n.f >= lo) & (n.f <= hi)]
        if best is None or inside.size > best.size:
            best = inside

    grid = best if best is not None and best.size >= 2 else np.linspace(lo, hi, 201)
    # Guarantee the endpoints so the overlap is fully covered.
    grid = np.unique(np.concatenate([[lo], grid, [hi]]))

    if collect_warnings is not None:
        for label, n in zip(labels, nets, strict=True):
            if float(n.f.min()) < lo - 1e-9 or float(n.f.max()) > hi + 1e-9:
                collect_warnings.append(
                    f"{label} spans {n.f.min() / 1e9:.6g}-{n.f.max() / 1e9:.6g} GHz; "
                    f"result restricted to the common range "
                    f"{lo / 1e9:.6g}-{hi / 1e9:.6g} GHz."
                )
    return grid


def cascade_networks(
    s2p_paths: Sequence[str | Path],
    output_path: str | Path,
    *,
    collect_warnings: list[str] | None = None,
) -> Path:
    """Cascade two or more 2-port networks (left-to-right order).

    Restricted to the frequency range common to every input; see
    :func:`common_frequency_grid`. Raises if the ranges are disjoint.
    """
    if len(s2p_paths) < 2:
        raise ValueError("Need at least 2 networks to cascade")
    nets = [read_touchstone(p) for p in s2p_paths]
    labels = [Path(p).name for p in s2p_paths]
    f_common = common_frequency_grid(nets, labels, collect_warnings=collect_warnings)
    fr = rf.Frequency.from_f(f_common, unit="Hz")
    nets = [n.interpolate(fr) for n in nets]
    cascaded = nets[0]
    for n in nets[1:]:
        cascaded = cascaded**n
    return write_touchstone(cascaded, output_path)


def deembed_network(
    measured_s2p: str | Path,
    fixture_left_s2p: str | Path,
    output_path: str | Path,
    fixture_right_s2p: str | Path | None = None,
    *,
    collect_warnings: list[str] | None = None,
) -> Path:
    """De-embed fixtures from a measured network: ``measured = L · DUT · R``.

    If ``fixture_right_s2p`` is omitted, both fixtures are assumed
    identical to ``fixture_left_s2p`` (symmetric calibration).

    Restricted to the frequency range common to the measurement and both
    fixtures; see :func:`common_frequency_grid`. Raises if they are disjoint.
    """
    meas = read_touchstone(measured_s2p)
    left = read_touchstone(fixture_left_s2p)
    right = read_touchstone(fixture_right_s2p) if fixture_right_s2p else left.copy()

    labels = [
        Path(measured_s2p).name,
        Path(fixture_left_s2p).name,
        Path(fixture_right_s2p).name
        if fixture_right_s2p
        else f"{Path(fixture_left_s2p).name} (right)",
    ]
    f_common = common_frequency_grid([meas, left, right], labels, collect_warnings=collect_warnings)
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
    return cast(NDArray[np.float64], 20.0 * np.log10(np.maximum(s21, 1e-12)))
