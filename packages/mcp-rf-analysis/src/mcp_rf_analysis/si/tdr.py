"""Time-domain transforms of S11 with explicit frequency-grid policy."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
from numpy.typing import NDArray

from rf_mcp_common.touchstone import read_touchstone

C0 = 299_792_458.0
TransformMode = Literal["lowpass", "bandpass"]
DcPolicy = Literal["constant", "linear", "zero", "reject"]
WindowName = Literal["hann", "rect"]


def _validate_frequency_grid(frequency_hz: NDArray[np.float64]) -> None:
    if frequency_hz.ndim != 1 or frequency_hz.size < 3:
        raise ValueError("TDR requires at least three one-dimensional frequency samples")
    if not np.all(np.isfinite(frequency_hz)) or np.any(frequency_hz < 0):
        raise ValueError("frequencies must be finite and non-negative")
    differences = np.diff(frequency_hz)
    if np.any(differences <= 0):
        raise ValueError("frequencies must be strictly increasing with no duplicates")


def _is_uniform(frequency_hz: NDArray[np.float64], tolerance: float) -> bool:
    differences = np.diff(frequency_hz)
    return bool(
        np.allclose(
            differences,
            differences[0],
            rtol=tolerance,
            atol=max(abs(differences[0]) * tolerance, 1e-15),
        )
    )


def _complex_interp(
    target_hz: NDArray[np.float64],
    source_hz: NDArray[np.float64],
    values: NDArray[np.complex128],
) -> NDArray[np.complex128]:
    return np.asarray(
        np.interp(target_hz, source_hz, values.real)
        + 1j * np.interp(target_hz, source_hz, values.imag),
        dtype=np.complex128,
    )


def _window(name: WindowName, size: int, *, lowpass: bool) -> NDArray[np.float64]:
    if name == "rect":
        return np.ones(size, dtype=float)
    if name != "hann":
        raise ValueError(f"window must be 'hann' or 'rect', got {name!r}")
    if lowpass:
        # Preserve DC and taper only the high-frequency truncation.
        return 0.5 * (1.0 + np.cos(np.linspace(0.0, np.pi, size)))
    return cast(NDArray[np.float64], np.hanning(size))


def _linear_dc_extrapolation(
    target_hz: NDArray[np.float64],
    measured_hz: NDArray[np.float64],
    measured: NDArray[np.complex128],
) -> NDArray[np.complex128]:
    slope = (measured[1] - measured[0]) / (measured_hz[1] - measured_hz[0])
    return np.asarray(measured[0] + slope * (target_hz - measured_hz[0]))


def tdr_transform(
    frequency_hz: NDArray[np.float64],
    s11: NDArray[np.complex128],
    *,
    z0_ohm: float = 50.0,
    er_eff: float = 4.0,
    transform_mode: TransformMode = "lowpass",
    window: WindowName = "hann",
    resample_nonuniform: bool = True,
    uniform_tolerance: float = 1e-6,
    dc_extrapolation: DcPolicy = "constant",
    padding_factor: int = 4,
    reference_plane_delay_s: float = 0.0,
    gate_time_s: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Transform S11 samples using a documented low-pass or band-pass policy.

    ``lowpass`` constructs a uniform DC-to-maximum-frequency one-sided
    spectrum and uses ``irfft`` (an explicit conjugate spectrum). It supports
    impedance step response, but any missing DC interval must use the selected
    ``dc_extrapolation`` policy.

    ``bandpass`` keeps only the measured bandwidth and uses a complex IFFT.
    It can locate reflections by envelope, but absolute impedance is
    indeterminate and is therefore not reported.
    """
    f = np.asarray(frequency_hz, dtype=float)
    gamma = np.asarray(s11, dtype=np.complex128)
    _validate_frequency_grid(f)
    if gamma.shape != f.shape or not np.all(np.isfinite(gamma)):
        raise ValueError("S11 must be finite and match the frequency grid")
    if z0_ohm <= 0 or er_eff <= 0:
        raise ValueError("z0_ohm and er_eff must be > 0")
    if padding_factor < 1 or padding_factor > 64:
        raise ValueError("padding_factor must be in [1, 64]")
    if reference_plane_delay_s < 0:
        raise ValueError("reference_plane_delay_s must be >= 0")
    if gate_time_s is not None and not 0 <= gate_time_s[0] < gate_time_s[1]:
        raise ValueError("gate_time_s must be (start, stop) with 0 <= start < stop")

    warnings: list[str] = []
    initially_uniform = _is_uniform(f, uniform_tolerance)
    if not initially_uniform:
        if not resample_nonuniform:
            raise ValueError(
                "frequency grid is non-uniform; enable resample_nonuniform "
                "or provide a uniform linear sweep"
            )
        warnings.append("non-uniform input resampled by linear complex interpolation")

    if reference_plane_delay_s:
        gamma = gamma * np.exp(1j * 4.0 * np.pi * f * reference_plane_delay_s)
        warnings.append(f"reference plane shifted by {reference_plane_delay_s:.6g} s one-way delay")

    velocity = C0 / np.sqrt(er_eff)
    measured_start = float(f[0])
    measured_stop = float(f[-1])

    if transform_mode == "lowpass":
        measured_step = float(np.median(np.diff(f)))
        n_positive = max(3, int(np.ceil(measured_stop / measured_step)) + 1)
        grid = np.linspace(0.0, measured_stop, n_positive)
        in_band = grid >= measured_start
        spectrum = np.empty(grid.size, dtype=np.complex128)
        spectrum[in_band] = _complex_interp(grid[in_band], f, gamma)

        if measured_start > measured_step * uniform_tolerance:
            missing = ~in_band
            if dc_extrapolation == "reject":
                raise ValueError(
                    f"low-pass transform requires DC, but sweep starts at {measured_start:g} Hz"
                )
            if dc_extrapolation == "constant":
                spectrum[missing] = gamma[0]
            elif dc_extrapolation == "linear":
                spectrum[missing] = _linear_dc_extrapolation(grid[missing], f, gamma)
            elif dc_extrapolation == "zero":
                spectrum[missing] = 0.0
            else:
                raise ValueError(f"unknown dc_extrapolation policy {dc_extrapolation!r}")
            warnings.append(
                f"DC-to-{measured_start:g} Hz gap filled using {dc_extrapolation} extrapolation"
            )
        else:
            spectrum[~in_band] = gamma[0]

        frequency_window = _window(window, spectrum.size, lowpass=True)
        base_time_size = 2 * (spectrum.size - 1)
        n_time = base_time_size * padding_factor
        impulse = np.fft.irfft(spectrum * frequency_window, n=n_time)
        impulse *= n_time / base_time_size
        df = float(grid[1] - grid[0])
        sample_interval = 1.0 / (n_time * df)
        time = np.arange(n_time, dtype=float) * sample_interval
        if gate_time_s is not None:
            gate = (time >= gate_time_s[0]) & (time <= gate_time_s[1])
            impulse = np.where(gate, impulse, 0.0)
            warnings.append(f"time gate applied over [{gate_time_s[0]:g}, {gate_time_s[1]:g}] s")
        rho = np.cumsum(impulse)
        rho_safe = np.clip(rho, -0.999, 0.999)
        impedance: list[float] | None = (z0_ohm * (1.0 + rho_safe) / (1.0 - rho_safe)).tolist()
        response = rho
        impulse_out: list[float] | list[list[float]] = impulse.tolist()
        physical_resolution_s = 1.0 / (2.0 * measured_stop)
        bandwidth_hz = measured_stop
        policy = "dc_to_fmax_conjugate_irfft"
    elif transform_mode == "bandpass":
        bandwidth_hz = measured_stop - measured_start
        if bandwidth_hz <= 0:
            raise ValueError("band-pass transform requires non-zero measured bandwidth")
        n_frequency = f.size
        grid = np.linspace(measured_start, measured_stop, n_frequency)
        spectrum = _complex_interp(grid, f, gamma)
        df = float(grid[1] - grid[0])
        n_time = n_frequency * padding_factor
        impulse_complex = np.fft.ifft(
            spectrum * _window(window, spectrum.size, lowpass=False),
            n=n_time,
        )
        impulse_complex *= n_time / n_frequency
        sample_interval = 1.0 / (n_time * df)
        time = np.arange(n_time, dtype=float) * sample_interval
        if gate_time_s is not None:
            gate = (time >= gate_time_s[0]) & (time <= gate_time_s[1])
            impulse_complex = np.where(gate, impulse_complex, 0.0)
            warnings.append(f"time gate applied over [{gate_time_s[0]:g}, {gate_time_s[1]:g}] s")
        response = np.abs(impulse_complex)
        impulse_out = [[float(value.real), float(value.imag)] for value in impulse_complex]
        impedance = None
        physical_resolution_s = 1.0 / bandwidth_hz
        policy = "measured_band_complex_ifft"
        warnings.append(
            "band-pass transform reports reflection envelope only; absolute impedance "
            "requires a low-pass/DC response"
        )
    else:
        raise ValueError(f"transform_mode must be 'lowpass' or 'bandpass', got {transform_mode!r}")

    unambiguous_time_s = 1.0 / df
    distance = time * velocity / 2.0
    return {
        "z0_ohm": z0_ohm,
        "er_eff": er_eff,
        "phase_velocity_m_s": float(velocity),
        "transform_mode": transform_mode,
        "transform_policy": policy,
        "window": window,
        "dc_extrapolation": dc_extrapolation if transform_mode == "lowpass" else None,
        "reference_plane_delay_s": reference_plane_delay_s,
        "gate_time_s": list(gate_time_s) if gate_time_s is not None else None,
        "input_grid": {
            "n_samples": int(f.size),
            "f_start_hz": measured_start,
            "f_stop_hz": measured_stop,
            "uniform_linear": initially_uniform,
            "resampled": not initially_uniform,
            "interpolation": "linear_complex" if not initially_uniform else None,
        },
        "transform_grid": {
            "n_frequency_samples": int(grid.size),
            "df_hz": df,
            "padding_factor": padding_factor,
            "n_time_samples": int(n_time),
        },
        "bandwidth_hz": bandwidth_hz,
        "time_sample_s": sample_interval,
        "time_resolution_s": physical_resolution_s,
        "distance_resolution_mm": physical_resolution_s * velocity / 2.0 * 1000.0,
        "unambiguous_time_s": unambiguous_time_s,
        "unambiguous_distance_mm": unambiguous_time_s * velocity / 2.0 * 1000.0,
        "time_ns": (time * 1e9).tolist(),
        "distance_mm": (distance * 1000.0).tolist(),
        "rho": response.tolist(),
        "rho_impulse": impulse_out,
        "impedance_ohm": impedance,
        "n_samples": int(n_time),
        "warnings": warnings,
    }


def tdr_from_s11(
    s2p_path: str | Path,
    *,
    er_eff: float = 4.0,
    transform_mode: TransformMode = "lowpass",
    window: WindowName = "hann",
    resample_nonuniform: bool = True,
    uniform_tolerance: float = 1e-6,
    dc_extrapolation: DcPolicy = "constant",
    padding_factor: int = 4,
    reference_plane_delay_s: float = 0.0,
    gate_time_s: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Load a Touchstone network and apply :func:`tdr_transform` to S11."""
    net = read_touchstone(s2p_path)
    if net.nports < 1:
        raise ValueError("Touchstone network has no ports")
    z0 = np.asarray(net.z0[:, 0], dtype=np.complex128)
    if not np.allclose(z0, z0[0], rtol=1e-6, atol=1e-9):
        raise ValueError("TDR requires a frequency-independent real reference impedance")
    if abs(z0[0].imag) > 1e-9:
        raise ValueError("TDR requires a real reference impedance")
    return tdr_transform(
        np.asarray(net.f, dtype=float),
        np.asarray(net.s[:, 0, 0], dtype=np.complex128),
        z0_ohm=float(z0[0].real),
        er_eff=er_eff,
        transform_mode=transform_mode,
        window=window,
        resample_nonuniform=resample_nonuniform,
        uniform_tolerance=uniform_tolerance,
        dc_extrapolation=dc_extrapolation,
        padding_factor=padding_factor,
        reference_plane_delay_s=reference_plane_delay_s,
        gate_time_s=gate_time_s,
    )
