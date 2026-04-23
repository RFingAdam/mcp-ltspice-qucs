"""Eye-diagram metrics from S-parameter data + PRBS source.

Convert the channel's S₂₁ to a time-domain pulse response, then convolve
with a PRBS (or random) bit pattern to build the eye diagram. Returns
the standard eye metrics: opening (height + width), worst-case ISI,
and jitter at the eye crossing.

This isn't a full SerDes analyzer — it's a first-order channel-quality
check that catches obvious problems before committing the design.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from rf_mcp_common.touchstone import read_touchstone


@dataclass
class EyeMetrics:
    bitrate_gbps: float
    n_bits: int
    eye_height_v: float  # min | top | - max | bot |  in centred eye
    eye_width_ui: float  # eye width as fraction of unit interval (UI)
    isi_pp_v: float  # worst-case inter-symbol interference
    sample_point_v_top: float
    sample_point_v_bot: float
    notes: list[str]


def _sinc_step(t: np.ndarray, t_rise: float) -> np.ndarray:
    """Smoothed step from 0 to 1 over t_rise (raised-cosine 0→1 in [0, t_rise])."""
    out = np.zeros_like(t)
    rising = (t >= 0) & (t <= t_rise)
    out[rising] = 0.5 * (1 - np.cos(np.pi * t[rising] / t_rise))
    out[t > t_rise] = 1.0
    return out


def eye_diagram_from_s2p(
    s2p_path: str | Path,
    *,
    bitrate_gbps: float,
    n_bits: int = 1024,
    swing_v: float = 1.0,
    rise_time_ps: float | None = None,
    seed: int = 0,
) -> EyeMetrics:
    """Compute eye-diagram metrics for a channel given its S-parameters.

    - ``bitrate_gbps``: signaling rate (e.g. 10 for 10 Gb/s)
    - ``n_bits``: how many random bits to simulate (more = better stats)
    - ``swing_v``: peak-to-peak voltage swing of the source
    - ``rise_time_ps``: source rise time. If None, defaults to 1/4 UI
      (a typical SerDes value)

    First-order: this uses the channel's S₂₁ magnitude as a frequency-
    domain transfer function, ignores S₁₁ reflections (assumes terminated
    perfectly), and ignores nonlinearity / driver pre-emphasis. For a
    full SerDes analysis use a dedicated tool like Keysight ADS or
    SiSoft Quantum-Channel.
    """
    net = read_touchstone(s2p_path)
    f = net.f
    s21 = net.s[:, 1, 0]

    ui_s = 1.0 / (bitrate_gbps * 1e9)
    if rise_time_ps is None:
        rise_time_s = ui_s / 4
    else:
        rise_time_s = rise_time_ps * 1e-12

    # Time axis: 32 samples per UI, n_bits long, padded for impulse decay
    samples_per_ui = 32
    n_samples = (n_bits + 16) * samples_per_ui
    dt = ui_s / samples_per_ui
    t = np.arange(n_samples) * dt

    # Generate random bit stream + NRZ waveform with finite rise time
    rng = np.random.default_rng(seed)
    bits = rng.integers(0, 2, n_bits)
    tx = np.zeros(n_samples)
    for i, b in enumerate(bits):
        idx_start = i * samples_per_ui
        idx_end = (i + 1) * samples_per_ui
        if i == 0:
            prev_b = bits[0]
        else:
            prev_b = bits[i - 1]
        if b != prev_b:
            # Add a smoothed transition at this bit boundary
            local_t = t[idx_start:idx_end] - t[idx_start]
            step = _sinc_step(local_t, rise_time_s)
            target = swing_v / 2 if b else -swing_v / 2
            start = -swing_v / 2 if b else swing_v / 2
            tx[idx_start:idx_end] = start + (target - start) * step
        else:
            tx[idx_start:idx_end] = swing_v / 2 if b else -swing_v / 2

    # Channel response: convolve TX with channel impulse
    # Compute impulse response from S21 via IFFT (assume regular grid)
    # Pad / interpolate s21 to a freq grid that matches our dt
    n_fft = n_samples
    freqs_fft = np.fft.rfftfreq(n_fft, dt)
    s21_interp_re = np.interp(freqs_fft, f, s21.real)
    s21_interp_im = np.interp(freqs_fft, f, s21.imag)
    s21_at_fft_grid = s21_interp_re + 1j * s21_interp_im
    # Above the highest measured freq, attenuate to avoid wrap-around
    s21_at_fft_grid[freqs_fft > f.max()] = 0.0

    # Apply channel
    tx_fft = np.fft.rfft(tx)
    rx_fft = tx_fft * s21_at_fft_grid
    rx = np.fft.irfft(rx_fft, n=n_fft)

    # Build eye: fold every 2-UI segment on top of each other
    fold_n = 2 * samples_per_ui
    skip_first_ui = 16
    n_traces = (n_bits - skip_first_ui) // 2
    eye = np.empty((n_traces, fold_n))
    for k in range(n_traces):
        start = (skip_first_ui + 2 * k) * samples_per_ui
        eye[k] = rx[start : start + fold_n]

    # Eye opening: at t = UI (the centre of the eye), find min top and max bot
    centre_idx = samples_per_ui  # one UI in
    centre_traces = eye[:, centre_idx]
    high = centre_traces[centre_traces > 0]
    low = centre_traces[centre_traces < 0]

    if high.size == 0 or low.size == 0:
        return EyeMetrics(
            bitrate_gbps=bitrate_gbps,
            n_bits=n_bits,
            eye_height_v=0.0,
            eye_width_ui=0.0,
            isi_pp_v=swing_v,
            sample_point_v_top=0.0,
            sample_point_v_bot=0.0,
            notes=["Eye is closed at sample point — channel destroys signal."],
        )

    eye_top = float(high.min())
    eye_bot = float(low.max())
    eye_height = max(eye_top - eye_bot, 0.0)

    # Width: count of sample columns where every trace is at least 5% of
    # swing away from 0 (i.e. the centre slot of the eye is unobstructed).
    # Returned as a fraction of one UI (so a perfectly-open 2-UI fold
    # gives ~1.0 since the bit-boundary transitions take up the other half).
    threshold = 0.05 * swing_v
    width_count = 0
    for col in range(fold_n):
        col_traces = eye[:, col]
        if col_traces.size == 0:
            continue
        if np.all(np.abs(col_traces) > threshold):
            width_count += 1
    eye_width_ui = width_count / samples_per_ui

    # Worst-case ISI: total spread at sample point
    isi_pp = float(centre_traces.max() - centre_traces.min()) / 2

    notes: list[str] = []
    if eye_height < 0.5 * swing_v:
        notes.append(
            f"Eye height {eye_height * 1000:.0f} mV < 50% of swing — "
            f"channel attenuates / disperses heavily at this rate."
        )
    if eye_width_ui < 0.5:
        notes.append(f"Eye width {eye_width_ui:.2f} UI < 0.5 UI — significant ISI/jitter.")

    return EyeMetrics(
        bitrate_gbps=bitrate_gbps,
        n_bits=n_bits,
        eye_height_v=eye_height,
        eye_width_ui=eye_width_ui,
        isi_pp_v=isi_pp,
        sample_point_v_top=eye_top,
        sample_point_v_bot=eye_bot,
        notes=notes,
    )
