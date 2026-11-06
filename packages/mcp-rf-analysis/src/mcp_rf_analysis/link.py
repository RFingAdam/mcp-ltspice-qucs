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
) -> dict[str, Any]:
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
) -> dict[str, Any]:
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
            notes.append("Small ground plane: added 6 dB coupling penalty")
    return {
        "freq_hz": freq_hz,
        "separation_m": antenna_separation_m,
        "isolation_db": iso_db,
        "wavelength_m": C0 / freq_hz,
        "notes": notes,
    }


#: Mechanisms accepted by :func:`compute_desense`. There is deliberately
#: no default: an out-of-band blocker treated as in-band noise overstates
#: desense by tens of dB, so the caller must say which physics applies.
DESENSE_MECHANISMS = ("in_band", "out_of_band_blocker", "reciprocal_mixing")

#: Inputs each mechanism needs (beyond aggressor power, filter rejection
#: and antenna isolation). ``out_of_band_blocker`` needs at least one of
#: the two receiver limits joined by ``|``.
DESENSE_REQUIRED_INPUTS: dict[str, tuple[str, ...]] = {
    "in_band": ("victim_noise_floor_dbm",),
    "out_of_band_blocker": ("victim_blocking_spec_dbm|victim_p1db_in_dbm",),
    "reciprocal_mixing": (
        "victim_noise_floor_dbm",
        "lo_phase_noise_dbc_hz",
        "victim_bandwidth_hz",
    ),
}


class DesenseInputError(ValueError):
    """Missing or invalid inputs for the declared desense mechanism.

    Carries machine-readable fields so the MCP layer can return a
    structured error instead of guessing a mechanism.
    """

    def __init__(self, message: str, *, mechanism: str | None, missing: list[str]) -> None:
        super().__init__(message)
        self.mechanism = mechanism
        self.missing = missing

    def details(self) -> dict[str, Any]:
        return {
            "error_type": "desense_input_error",
            "mechanism": self.mechanism,
            "missing_inputs": self.missing,
            "valid_mechanisms": list(DESENSE_MECHANISMS),
            "required_inputs": {k: list(v) for k, v in DESENSE_REQUIRED_INPUTS.items()},
        }


def _noise_rise_db(i_dbm: float, n_dbm: float) -> float:
    """Noise-floor rise from adding interference I to noise N (both dBm in
    the same bandwidth): 10*log10(1 + 10^((I-N)/10))."""
    return 10.0 * math.log10(1.0 + 10.0 ** ((i_dbm - n_dbm) / 10.0))


def _noise_concern(desense_db: float) -> str:
    if desense_db < 0.25:
        return "none"
    if desense_db < 1.0:
        return "low"
    if desense_db < 3.0:
        return "medium"
    if desense_db < 10.0:
        return "high"
    return "critical"


def _margin_concern(margin_db: float) -> str:
    if margin_db > 10:
        return "none"
    if margin_db > 3:
        return "low"
    if margin_db > 0:
        return "medium"
    if margin_db > -10:
        return "high"
    return "critical"


def compute_desense(
    aggressor_power_dbm: float,
    filter_rejection_db: float,
    antenna_iso_db: float,
    victim_noise_floor_dbm: float | None = None,
    *,
    mechanism: str | None = None,
    victim_blocking_spec_dbm: float | None = None,
    victim_p1db_in_dbm: float | None = None,
    lo_phase_noise_dbc_hz: float | None = None,
    victim_bandwidth_hz: float | None = None,
    victim_sensitivity_dbm: float | None = None,
    max_desense_db: float = 1.0,
) -> dict[str, Any]:
    """Predict the impact of an aggressor TX on a victim RX for ONE declared
    coupling mechanism.

    In every mechanism the aggressor power arriving at the victim RX input is

        P_rx = aggressor_power_dbm - filter_rejection_db - antenna_iso_db

    where ``filter_rejection_db`` is whatever filtering applies to the
    aggressor at the frequency in question (TX harmonic filter for an
    in-band spur, victim RX front-end filter for an out-of-band blocker).

    ``mechanism`` is required (no default; a missing or unknown value
    raises :class:`DesenseInputError`):

    ``in_band``
        The aggressor energy (spur, harmonic, TX noise) lands inside the
        victim channel and adds to its noise floor. Needs
        ``victim_noise_floor_dbm`` = integrated noise in the victim channel
        bandwidth (-174 dBm/Hz + 10log10(BW) + NF), *not* the sensitivity.

            desense_db = 10*log10(1 + 10^((P_rx - N)/10))
            desense_margin_db = max_desense_db - desense_db

    ``out_of_band_blocker``
        The aggressor is outside the victim channel. It does NOT add to the
        noise floor; it degrades the RX only through blocking / gain
        compression. Needs at least one of ``victim_blocking_spec_dbm``
        (largest blocker the RX tolerates at that offset per its spec or
        datasheet, at the spec's allowed sensitivity degradation) or
        ``victim_p1db_in_dbm`` (input-referred 1 dB compression point).

            blocking_margin_db    = victim_blocking_spec_dbm - P_rx
            compression_margin_db = victim_p1db_in_dbm - P_rx
            desense_margin_db     = min(available margins)

        No noise-floor desense number is produced (``desense_db`` is None).

    ``reciprocal_mixing``
        The blocker mixes with the victim LO phase noise and lands in the
        IF channel. Needs ``lo_phase_noise_dbc_hz`` (LO phase noise at the
        blocker frequency offset), ``victim_bandwidth_hz`` (channel noise
        bandwidth) and ``victim_noise_floor_dbm``.

            I_rm = P_rx + lo_phase_noise_dbc_hz + 10*log10(victim_bandwidth_hz)

        I_rm is then treated as in-band noise exactly as ``in_band``.

    Transmitter broadband noise falling in the victim channel is an
    ``in_band`` case: pass the TX noise power integrated over the victim
    bandwidth as ``aggressor_power_dbm``.

    ``desense_margin_db`` is positive when there is no concern and
    negative when the budget or spec is exceeded.
    """
    if mechanism is None or mechanism not in DESENSE_MECHANISMS:
        what = "missing" if mechanism is None else f"unknown ({mechanism!r})"
        raise DesenseInputError(
            f"mechanism is {what}: declare one of {list(DESENSE_MECHANISMS)}. "
            "An out-of-band blocker must not be treated as in-band noise.",
            mechanism=mechanism,
            missing=["mechanism"],
        )

    p_rx = aggressor_power_dbm - filter_rejection_db - antenna_iso_db
    base: dict[str, Any] = {
        "mechanism": mechanism,
        "aggressor_power_dbm": aggressor_power_dbm,
        "filter_rejection_db": filter_rejection_db,
        "antenna_iso_db": antenna_iso_db,
        "received_at_rx_dbm": p_rx,
    }
    if victim_sensitivity_dbm is not None:
        base["victim_sensitivity_dbm"] = victim_sensitivity_dbm

    if mechanism == "out_of_band_blocker":
        if victim_blocking_spec_dbm is None and victim_p1db_in_dbm is None:
            raise DesenseInputError(
                "out_of_band_blocker needs victim_blocking_spec_dbm (receiver blocking "
                "spec at this offset) and/or victim_p1db_in_dbm (input P1dB). An "
                "out-of-band blocker does not add to the noise floor, so it cannot be "
                "compared with the noise floor or the sensitivity.",
                mechanism=mechanism,
                missing=["victim_blocking_spec_dbm|victim_p1db_in_dbm"],
            )
        margins: dict[str, float] = {}
        if victim_blocking_spec_dbm is not None:
            margins["blocking_margin_db"] = victim_blocking_spec_dbm - p_rx
        if victim_p1db_in_dbm is not None:
            margins["compression_margin_db"] = victim_p1db_in_dbm - p_rx
        margin = min(margins.values())
        return {
            **base,
            "victim_blocking_spec_dbm": victim_blocking_spec_dbm,
            "victim_p1db_in_dbm": victim_p1db_in_dbm,
            **margins,
            "adds_to_noise_floor": False,
            "desense_db": None,
            "desense_margin_db": margin,
            "concern_level": _margin_concern(margin),
            "formula": (
                "P_rx = P_aggr - filter_rejection - antenna_iso; "
                "margin = min(blocking_spec - P_rx, P1dB_in - P_rx); "
                "no noise-floor contribution"
            ),
        }

    missing: list[str] = []
    if victim_noise_floor_dbm is None:
        missing.append("victim_noise_floor_dbm")
    if mechanism == "reciprocal_mixing":
        if lo_phase_noise_dbc_hz is None:
            missing.append("lo_phase_noise_dbc_hz")
        if victim_bandwidth_hz is None:
            missing.append("victim_bandwidth_hz")
        elif victim_bandwidth_hz <= 0:
            raise DesenseInputError(
                "victim_bandwidth_hz must be positive",
                mechanism=mechanism,
                missing=["victim_bandwidth_hz"],
            )
    if missing or victim_noise_floor_dbm is None:
        raise DesenseInputError(
            f"{mechanism} needs {missing}",
            mechanism=mechanism,
            missing=missing,
        )

    extra: dict[str, Any] = {}
    if mechanism == "reciprocal_mixing":
        if lo_phase_noise_dbc_hz is None or victim_bandwidth_hz is None:  # pragma: no cover
            raise DesenseInputError("unreachable", mechanism=mechanism, missing=missing)
        i_dbm = p_rx + lo_phase_noise_dbc_hz + 10.0 * math.log10(victim_bandwidth_hz)
        extra = {
            "lo_phase_noise_dbc_hz": lo_phase_noise_dbc_hz,
            "victim_bandwidth_hz": victim_bandwidth_hz,
            "reciprocal_mixing_power_dbm": i_dbm,
        }
        formula = "I_rm = P_rx + PN_dBc/Hz + 10log10(BW); desense = 10log10(1 + 10^((I_rm - N)/10))"
    else:
        i_dbm = p_rx
        formula = "desense = 10log10(1 + 10^((P_rx - N)/10)), P_rx inside the victim channel"

    desense = _noise_rise_db(i_dbm, victim_noise_floor_dbm)
    margin = max_desense_db - desense
    return {
        **base,
        **extra,
        "victim_noise_floor_dbm": victim_noise_floor_dbm,
        "interference_dbm": i_dbm,
        "i_over_n_db": i_dbm - victim_noise_floor_dbm,
        "adds_to_noise_floor": True,
        "desense_db": desense,
        "max_desense_db": max_desense_db,
        "desense_margin_db": margin,
        "concern_level": _noise_concern(desense),
        "formula": formula,
    }
