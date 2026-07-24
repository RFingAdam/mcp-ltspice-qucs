"""Touchstone diff / delay / equivalent-circuit fitting utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import numpy as np
import skrf as rf
from numpy.typing import NDArray

from rf_mcp_common.touchstone import read_touchstone


def compare_sparameters(
    s2p_a: str | Path, s2p_b: str | Path, *, metric: str = "s21_db"
) -> dict[str, Any]:
    """Compute element-wise difference between two S-parameter files.

    ``metric`` ∈ {'s21_db', 's11_db', 'mag_s21', 'phase_s21_deg'}.
    Both files are linearly interpolated in Cartesian complex coordinates to
    the union of their measured points inside the overlapping frequency span.
    No extrapolation is allowed. Phase difference uses the shortest circular
    distance in ``[-180, 180)`` degrees, so ``179°`` versus ``-179°`` is a
    2-degree difference rather than 358 degrees.
    """
    a = read_touchstone(s2p_a)
    b = read_touchstone(s2p_b)
    _validate_comparison_network(a, str(s2p_a))
    _validate_comparison_network(b, str(s2p_b))
    f_common, overlap = _comparison_frequency_grid(a, b, str(s2p_a), str(s2p_b))
    fr = rf.Frequency.from_f(f_common, unit="Hz")
    a_i = a.interpolate(fr, basis="s", coords="cart", kind="linear")
    b_i = b.interpolate(fr, basis="s", coords="cart", kind="linear")

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

    if metric == "phase_s21_deg":
        diff = _circular_difference_deg(b_v, a_v)
    else:
        diff = b_v - a_v
    result: dict[str, Any] = {
        "metric": metric,
        "freq_hz": f_common.tolist(),
        "a_values": a_v.tolist(),
        "b_values": b_v.tolist(),
        "diff": diff.tolist(),
        "max_abs_diff": float(np.max(np.abs(diff))),
        "mean_abs_diff": float(np.mean(np.abs(diff))),
        "rms_diff": float(np.sqrt(np.mean(diff**2))),
        "frequency_policy": "union_within_overlap",
        "overlap_hz": list(overlap),
        "input_ranges_hz": {
            "a": [float(a.f.min()), float(a.f.max())],
            "b": [float(b.f.min()), float(b.f.max())],
        },
        "interpolation": {
            "basis": "s",
            "coordinates": "cartesian_complex",
            "kind": "linear",
            "extrapolation": False,
        },
    }
    if metric == "phase_s21_deg":
        result["phase_difference_mode"] = "circular_shortest_degrees"
    return result


def _validate_comparison_network(network: rf.Network, label: str) -> None:
    if network.nports < 2:
        raise ValueError(f"{label} must contain at least two ports; got {network.nports}")
    frequencies = np.asarray(network.f, dtype=float)
    if frequencies.size < 2:
        raise ValueError(f"{label} must contain at least two frequency points")
    if not np.all(np.isfinite(frequencies)):
        raise ValueError(f"{label} contains non-finite frequency values")
    if not np.all(np.diff(frequencies) > 0):
        raise ValueError(f"{label} frequencies must be strictly increasing")


def _comparison_frequency_grid(
    a: rf.Network,
    b: rf.Network,
    label_a: str,
    label_b: str,
) -> tuple[NDArray[np.float64], tuple[float, float]]:
    lo = max(float(a.f.min()), float(b.f.min()))
    hi = min(float(a.f.max()), float(b.f.max()))
    if lo >= hi:
        raise ValueError(
            "Frequency ranges do not overlap, so S-parameters cannot be compared "
            f"without extrapolation. Ranges — {label_a}: "
            f"{a.f.min():.9g}-{a.f.max():.9g} Hz; {label_b}: "
            f"{b.f.min():.9g}-{b.f.max():.9g} Hz."
        )

    a_inside = a.f[(a.f >= lo) & (a.f <= hi)]
    b_inside = b.f[(b.f >= lo) & (b.f <= hi)]
    grid = np.unique(np.concatenate(([lo], a_inside, b_inside, [hi])))
    if grid.size < 2:
        raise ValueError(
            f"Frequency overlap {lo:.9g}-{hi:.9g} Hz does not contain enough points to compare"
        )
    return np.asarray(grid, dtype=np.float64), (lo, hi)


def _circular_difference_deg(
    minuend: NDArray[np.float64],
    subtrahend: NDArray[np.float64],
) -> NDArray[np.float64]:
    return np.asarray((minuend - subtrahend + 180.0) % 360.0 - 180.0, dtype=np.float64)


def extract_delay(
    s2p_path: str | Path,
    method: str = "group_delay",
    *,
    analysis_band_hz: tuple[float, float] | None = None,
) -> dict[str, Any]:
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
        result: dict[str, Any] = {
            "freq_hz": f.tolist(),
            "group_delay_s": gd.tolist(),
            "method": "negative_phase_derivative",
            "analysis_band_hz": (list(analysis_band_hz) if analysis_band_hz is not None else None),
            "warnings": [],
        }
        if analysis_band_hz is None:
            result["warnings"].append(
                "No analysis_band_hz supplied; no passband/summary delay claim was computed."
            )
        else:
            low, high = analysis_band_hz
            if not 0 <= low < high:
                raise ValueError("analysis_band_hz must satisfy 0 <= low < high")
            if low < f[0] or high > f[-1]:
                raise ValueError(
                    f"analysis band [{low}, {high}] Hz is outside measured range "
                    f"[{f[0]}, {f[-1]}] Hz"
                )
            mask = (f >= low) & (f <= high)
            if np.count_nonzero(mask) < 2:
                raise ValueError("analysis band contains fewer than two measured points")
            band_values = gd[mask]
            result["band_mean_delay_s"] = float(np.mean(band_values))
            result["band_min_delay_s"] = float(np.min(band_values))
            result["band_max_delay_s"] = float(np.max(band_values))
            result["band_peak_to_peak_delay_s"] = float(np.ptp(band_values))
        return result
    if method == "unwrapped_phase":
        return {
            "freq_hz": f.tolist(),
            "phase_rad": phase_unwrapped.tolist(),
            "phase_deg": np.degrees(phase_unwrapped).tolist(),
        }
    raise ValueError(f"Unknown method: {method}")


def fit_equivalent_circuit(
    s2p_path: str | Path,
    *,
    topology: str = "series_l_shunt_c",
    fit_band_hz: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Fit a simple lumped equivalent circuit to a 2-port network.

    Supported topologies:
    - ``series_l``: a single series inductor (returns L)
    - ``shunt_c``: a single shunt capacitor (returns C)
    - ``series_l_shunt_c``: a single L-section LPF (returns L, C)
    """
    from scipy.optimize import least_squares

    net = read_touchstone(s2p_path)
    if fit_band_hz is not None:
        low, high = fit_band_hz
        if not 0 <= low < high or low < net.f[0] or high > net.f[-1]:
            raise ValueError(f"fit_band_hz must lie inside [{net.f[0]}, {net.f[-1]}] Hz")
        mask = (net.f >= low) & (net.f <= high)
        if np.count_nonzero(mask) < 3:
            raise ValueError("fit band must contain at least three frequency points")
        net = net[mask]
    omega: NDArray[np.float64] = 2 * np.pi * net.f
    z0 = float(net.z0[0, 0].real)
    target_s21 = net.s[:, 1, 0]

    def finish(
        out: Any,
        modeled: NDArray[np.complex128],
        values: dict[str, float],
        bounds: dict[str, list[float]],
    ) -> dict[str, Any]:
        residual = modeled - target_s21
        magnitude = np.abs(residual)
        jacobian = np.asarray(out.jac, dtype=float)
        singular = np.linalg.svd(jacobian, compute_uv=False)
        condition = (
            float(singular[0] / singular[-1])
            if singular.size and singular[-1] > 0
            else float("inf")
        )
        identifiable = bool(np.isfinite(condition) and condition < 1e10)
        return {
            "topology": topology,
            **values,
            "solver": {
                "success": bool(out.success),
                "status": int(out.status),
                "message": str(out.message),
                "n_function_evaluations": int(out.nfev),
                "cost": float(out.cost),
                "optimality": float(out.optimality),
            },
            "fit_quality": {
                "complex_rmse": float(np.sqrt(np.mean(magnitude**2))),
                "max_complex_residual": float(np.max(magnitude)),
                "median_complex_residual": float(np.median(magnitude)),
                "normalized_rmse": float(
                    np.sqrt(np.mean(magnitude**2))
                    / max(float(np.sqrt(np.mean(np.abs(target_s21) ** 2))), 1e-15)
                ),
                "jacobian_condition_number": condition,
                "identifiable": identifiable,
            },
            "bounds": bounds,
            "valid_frequency_hz": [float(net.f[0]), float(net.f[-1])],
            "n_frequency_points": int(net.f.size),
            "method": "bounded_complex_least_squares_s21",
            "warnings": (
                []
                if identifiable and out.success
                else ["Fit is not converged or parameters are poorly identifiable."]
            ),
        }

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
        return cast(NDArray[np.complex128], 2.0 / denom)

    if topology == "series_l":

        def res(x: NDArray[np.float64]) -> NDArray[np.float64]:
            return np.concatenate(
                [
                    (_model_series_l(x[0]) - target_s21).real,
                    (_model_series_l(x[0]) - target_s21).imag,
                ]
            )

        out = least_squares(res, [1e-9], bounds=(1e-15, 1e-3))
        return finish(
            out,
            cast(NDArray[np.complex128], _model_series_l(out.x[0])),
            {"L": float(out.x[0])},
            {"L": [1e-15, 1e-3]},
        )

    if topology == "shunt_c":

        def res(x: NDArray[np.float64]) -> NDArray[np.float64]:
            return np.concatenate(
                [(_model_shunt_c(x[0]) - target_s21).real, (_model_shunt_c(x[0]) - target_s21).imag]
            )

        out = least_squares(res, [1e-12], bounds=(1e-18, 1e-6))
        return finish(
            out,
            cast(NDArray[np.complex128], _model_shunt_c(out.x[0])),
            {"C": float(out.x[0])},
            {"C": [1e-18, 1e-6]},
        )

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
        return finish(
            out,
            _model_l_section(out.x[0], out.x[1]),
            {"L": float(out.x[0]), "C": float(out.x[1])},
            {"L": [1e-15, 1e-3], "C": [1e-18, 1e-6]},
        )

    raise ValueError(f"Unknown topology: {topology}")
