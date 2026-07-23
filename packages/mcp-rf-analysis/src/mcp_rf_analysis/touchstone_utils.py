"""Touchstone diff / delay / equivalent-circuit fitting utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import skrf as rf
from numpy.typing import NDArray

from rf_mcp_common.touchstone import read_touchstone


def compare_sparameters(
    s2p_a: str | Path, s2p_b: str | Path, *, metric: str = "s21_db"
) -> dict[str, Any]:
    """Compute element-wise difference between two S-parameter files.

    ``metric`` ∈ {'s21_db', 's11_db', 'mag_s21', 'phase_s21_deg'}.
    Both files are interpolated to the common frequency grid.
    """
    a = read_touchstone(s2p_a)
    b = read_touchstone(s2p_b)
    f_common = np.intersect1d(a.f, b.f)
    if f_common.size < 2:
        # Use finer of the two and interpolate the other
        f_common = a.f
    fr = rf.Frequency.from_f(f_common, unit="Hz")
    a_i = a.interpolate(fr)
    b_i = b.interpolate(fr)

    if metric == "s21_db":
        a_v = 20 * np.log10(np.maximum(np.abs(a_i.s[:, 1, 0]), 1e-12))
        b_v = 20 * np.log10(np.maximum(np.abs(b_i.s[:, 1, 0]), 1e-12))
    elif metric == "s11_db":
        a_v = 20 * np.log10(np.maximum(np.abs(a_i.s[:, 0, 0]), 1e-12))
        b_v = 20 * np.log10(np.maximum(np.abs(b_i.s[:, 0, 0]), 1e-12))
    elif metric == "mag_s21":
        a_v = np.abs(a_i.s[:, 1, 0])
        b_v = np.abs(b_i.s[:, 1, 0])
    elif metric == "phase_s21_deg":
        a_v = np.angle(a_i.s[:, 1, 0], deg=True)
        b_v = np.angle(b_i.s[:, 1, 0], deg=True)
    else:
        raise ValueError(f"Unknown metric: {metric}")

    diff = b_v - a_v
    return {
        "metric": metric,
        "freq_hz": f_common.tolist(),
        "a_values": a_v.tolist(),
        "b_values": b_v.tolist(),
        "diff": diff.tolist(),
        "max_abs_diff": float(np.max(np.abs(diff))),
        "mean_abs_diff": float(np.mean(np.abs(diff))),
        "rms_diff": float(np.sqrt(np.mean(diff**2))),
    }


def extract_delay(s2p_path: str | Path, method: str = "group_delay") -> dict[str, Any]:
    """Compute group delay or transit-time delay for S21.

    - ``group_delay``: τ_g(ω) = -dφ/dω (computed via gradient of unwrapped phase)
    - ``unwrapped_phase``: returns the unwrapped phase array directly
    """
    net = read_touchstone(s2p_path)
    f = net.f
    s21 = net.s[:, 1, 0]
    phase_unwrapped = np.unwrap(np.angle(s21))
    omega = 2 * np.pi * f
    if method == "group_delay":
        # dφ/dω: use central differences via np.gradient
        gd = -np.gradient(phase_unwrapped, omega)
        return {
            "freq_hz": f.tolist(),
            "group_delay_s": gd.tolist(),
            "mean_delay_s": float(np.mean(gd)),
            "passband_delay_s": float(np.mean(gd[: len(gd) // 2])),
        }
    if method == "unwrapped_phase":
        return {
            "freq_hz": f.tolist(),
            "phase_rad": phase_unwrapped.tolist(),
            "phase_deg": np.degrees(phase_unwrapped).tolist(),
        }
    raise ValueError(f"Unknown method: {method}")


def fit_equivalent_circuit(
    s2p_path: str | Path, *, topology: str = "series_l_shunt_c"
) -> dict[str, Any]:
    """Fit a simple lumped equivalent circuit to a 2-port network.

    Supported topologies:
    - ``series_l``: a single series inductor (returns L)
    - ``shunt_c``: a single shunt capacitor (returns C)
    - ``series_l_shunt_c``: a single L-section LPF (returns L, C)
    """
    from scipy.optimize import least_squares

    net = read_touchstone(s2p_path)
    omega = 2 * np.pi * net.f
    z0 = float(net.z0[0, 0].real)
    target_s21 = net.s[:, 1, 0]

    def _model_series_l(l_h: float) -> np.ndarray:
        z = 1j * omega * l_h
        return 2.0 * z0 / (z + 2 * z0)  # voltage divider with source / load Z0

    def _model_shunt_c(c_f: float) -> np.ndarray:
        y = 1j * omega * c_f
        return 2.0 / (2 + z0 * y)

    def _model_l_section(l_h: float, c_f: float) -> np.ndarray:
        # ABCD: series L then shunt C
        zl = 1j * omega * l_h
        yc = 1j * omega * c_f
        a = 1 + zl * yc
        b = zl
        c = yc
        d = np.ones_like(omega) + 0j
        denom = a + b / z0 + c * z0 + d
        return 2.0 / denom

    if topology == "series_l":

        def res(x: NDArray[np.float64]) -> NDArray[np.float64]:
            return np.concatenate(
                [
                    (_model_series_l(x[0]) - target_s21).real,
                    (_model_series_l(x[0]) - target_s21).imag,
                ]
            )

        out = least_squares(res, [1e-9], bounds=(1e-15, 1e-3))
        return {"topology": topology, "L": float(out.x[0])}

    if topology == "shunt_c":

        def res(x: NDArray[np.float64]) -> NDArray[np.float64]:
            return np.concatenate(
                [(_model_shunt_c(x[0]) - target_s21).real, (_model_shunt_c(x[0]) - target_s21).imag]
            )

        out = least_squares(res, [1e-12], bounds=(1e-18, 1e-6))
        return {"topology": topology, "C": float(out.x[0])}

    if topology == "series_l_shunt_c":

        def res(x: NDArray[np.float64]) -> NDArray[np.float64]:
            return np.concatenate(
                [
                    (_model_l_section(x[0], x[1]) - target_s21).real,
                    (_model_l_section(x[0], x[1]) - target_s21).imag,
                ]
            )

        out = least_squares(
            res,
            [1e-9, 1e-12],
            bounds=([1e-15, 1e-18], [1e-3, 1e-6]),
        )
        return {"topology": topology, "L": float(out.x[0]), "C": float(out.x[1])}

    raise ValueError(f"Unknown topology: {topology}")
