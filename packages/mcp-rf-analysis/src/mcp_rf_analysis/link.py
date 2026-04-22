"""Link budget and antenna isolation helpers."""

from __future__ import annotations

import math
from typing import Any

C0 = 299_792_458.0  # m/s


def db_minus(a_dbm: float, b_db: float) -> float:
    """Power - attenuation = remaining power. Tiny convenience function."""
    return a_dbm - b_db


def compute_path_loss(
    freq_hz: float,
    distance_m: float,
    *,
    model: str = "friis",
    n: float = 2.0,
    extra_loss_db: float = 0.0,
) -> dict[str, float]:
    """Compute free-space (Friis) or log-distance path loss.

    - ``friis``: PL_dB = 20 log10(4π d f / c)
    - ``log_distance``: PL_dB = PL_ref(1m) + 10 n log10(d / 1m)
    """
    if freq_hz <= 0 or distance_m <= 0:
        raise ValueError("freq_hz and distance_m must be positive")
    if model == "friis":
        wavelength = C0 / freq_hz
        pl_db = 20.0 * math.log10(4.0 * math.pi * distance_m / wavelength)
    elif model == "log_distance":
        pl_ref = 20.0 * math.log10(4.0 * math.pi * 1.0 / (C0 / freq_hz))
        pl_db = pl_ref + 10.0 * n * math.log10(distance_m)
    else:
        raise ValueError(f"Unknown model: {model}")
    return {
        "freq_hz": freq_hz,
        "distance_m": distance_m,
        "model": model,
        "path_loss_db": pl_db + extra_loss_db,
        "wavelength_m": C0 / freq_hz,
    }


def compute_antenna_isolation_estimate(
    antenna_separation_m: float,
    freq_hz: float,
    *,
    ground_plane_size_m: float | None = None,
) -> dict[str, float]:
    """Estimate antenna-to-antenna isolation in free space + ground plane.

    Uses far-field Friis as a baseline. If ``ground_plane_size_m`` is
    provided and is small relative to the wavelength, an additional
    ~6 dB penalty (parasitic coupling) is applied as a conservative
    estimate.
    """
    if antenna_separation_m <= 0:
        raise ValueError("antenna_separation_m must be positive")
    base_loss = compute_path_loss(freq_hz, antenna_separation_m, model="friis")
    iso_db = base_loss["path_loss_db"]
    notes: list[str] = []
    if ground_plane_size_m is not None:
        wavelength = C0 / freq_hz
        if ground_plane_size_m < wavelength / 4:
            iso_db -= 6.0
            notes.append("Small ground plane — added 6 dB coupling penalty")
    return {
        "freq_hz": freq_hz,
        "separation_m": antenna_separation_m,
        "isolation_db": iso_db,
        "wavelength_m": C0 / freq_hz,
        "notes": notes,
    }


def compute_desense(
    aggressor_power_dbm: float,
    filter_rejection_db: float,
    antenna_iso_db: float,
    victim_noise_floor_dbm: float,
) -> dict[str, Any]:
    """Predict the impact of an aggressor TX on a victim RX.

    Received aggressor power at victim:
        P_rx = P_tx - filter_rejection - antenna_iso

    If P_rx is well below the victim noise floor, no degradation. If it
    is comparable to or above noise floor, RX sensitivity is degraded.

    Returns:
        - ``received_at_rx_dbm``
        - ``snr_margin_db`` (positive = aggressor below noise; negative = aggressor dominates)
        - ``desense_margin_db`` (positive = no concern, negative = blocking)
        - ``concern_level``: 'none' | 'low' | 'medium' | 'high' | 'critical'
    """
    p_rx = aggressor_power_dbm - filter_rejection_db - antenna_iso_db
    snr_margin = victim_noise_floor_dbm - p_rx
    if snr_margin > 10:
        concern = "none"
    elif snr_margin > 0:
        concern = "low"
    elif snr_margin > -10:
        concern = "medium"
    elif snr_margin > -30:
        concern = "high"
    else:
        concern = "critical"
    return {
        "aggressor_power_dbm": aggressor_power_dbm,
        "filter_rejection_db": filter_rejection_db,
        "antenna_iso_db": antenna_iso_db,
        "received_at_rx_dbm": p_rx,
        "victim_noise_floor_dbm": victim_noise_floor_dbm,
        "snr_margin_db": snr_margin,
        "desense_margin_db": snr_margin,
        "concern_level": concern,
    }
