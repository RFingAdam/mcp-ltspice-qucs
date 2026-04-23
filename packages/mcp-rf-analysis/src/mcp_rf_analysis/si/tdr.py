"""Time-Domain Reflectometry from S-parameter data.

Convert frequency-domain S₁₁ to time-domain reflection profile via
inverse FFT, then map time to physical distance using the substrate's
phase velocity.

Useful for:
- Locating impedance discontinuities (a connector, a stub, a missing via)
- Estimating connector / cable length
- Verifying that an impedance-matched run actually is matched everywhere
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from rf_mcp_common.touchstone import read_touchstone

C0 = 299_792_458.0  # m/s


def tdr_from_s11(
    s2p_path: str | Path,
    *,
    er_eff: float = 4.0,  # effective dielectric constant
    window: str = "hann",  # FFT window for sidelobe suppression
) -> dict[str, Any]:
    """Compute the impedance-vs-distance profile from an S₁₁ trace.

    Steps:
    1. Take S₁₁(f) from the Touchstone file
    2. Pad with zeros for higher time resolution
    3. Apply window (Hann default) to reduce ringing
    4. IFFT → time-domain step response
    5. Convert reflection coefficient ρ(t) to impedance Z(t) via
       Z = Z₀ · (1+ρ)/(1-ρ)
    6. Convert time to distance via v_p = c / √ε_eff

    Returns:
        ``time_ns``, ``distance_mm``, ``rho``, ``impedance_ohm``
    """
    net = read_touchstone(s2p_path)
    f = net.f
    s11 = net.s[:, 0, 0]
    z0 = float(net.z0[0, 0].real)

    # Apply window
    if window == "hann":
        win = np.hanning(s11.size)
    else:
        win = np.ones_like(s11)
    s11_w = s11 * win

    # Zero-pad to next power of 2 × 4 for finer time resolution
    n_padded = 2 ** int(np.ceil(np.log2(s11.size * 4)))
    s11_padded = np.zeros(n_padded, dtype=np.complex128)
    s11_padded[: s11.size] = s11_w

    # IFFT to get impulse response
    h = np.fft.ifft(s11_padded).real
    # Step response = cumulative sum of impulse
    rho_step = np.cumsum(h)

    # Time axis from frequency span
    df = f[1] - f[0]
    t = np.arange(n_padded) / (n_padded * df)

    # Distance: round-trip through media at v_p = c/√εeff
    # One-way distance = (t/2) · v_p
    v_p = C0 / np.sqrt(er_eff)
    distance_mm = (t / 2) * v_p * 1000.0

    # Impedance from reflection coefficient
    # Clamp |rho| < 1 for stability
    rho_safe = np.clip(rho_step, -0.999, 0.999)
    z = z0 * (1 + rho_safe) / (1 - rho_safe)

    # Trim trailing zero-pad artifacts: keep first n_padded // 4 samples
    n_keep = n_padded // 4
    return {
        "z0_ohm": z0,
        "er_eff": er_eff,
        "phase_velocity_m_s": float(v_p),
        "time_ns": (t[:n_keep] * 1e9).tolist(),
        "distance_mm": distance_mm[:n_keep].tolist(),
        "rho": rho_step[:n_keep].tolist(),
        "impedance_ohm": z[:n_keep].tolist(),
        "n_samples": n_keep,
    }
