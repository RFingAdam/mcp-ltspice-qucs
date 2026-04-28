"""SMPS EMC pre-compliance helpers.

Five design / prediction tools that fill the gap between the existing
buck / boost / LDO sizing and a real product passing conducted-emissions:

- :func:`design_pi_output_filter` — Pi-section LC filter (C-L-C) for
  additional output-ripple attenuation downstream of the converter's
  built-in Cout. Returns L, Cin, Cout values, predicted attenuation, and
  resonant frequency with a damping recommendation.
- :func:`design_dm_input_filter` — 2nd-order DM input filter sized for a
  conducted-emissions target. Includes the Middlebrook stability
  criterion check (|Z_out,filter| < |Z_in,converter|) so the filter
  doesn't destabilise the loop.
- :func:`predict_conducted_emissions` — harmonic decomposition of a
  trapezoidal switching waveform, LISN-loaded prediction, CISPR 22 / 32
  Class A / B limit overlay, margin per harmonic.
- :func:`design_rc_snubber` — RC snubber for switch-node ringing
  (parasitic L_loop with Coss). Returns R, C values, damping factor,
  and dissipation per cycle.
- :func:`design_cm_choke` — common-mode choke sizing helper. Given DC
  current, target CM impedance and frequency, picks from a small
  curated catalogue and reports DM leakage inductance.

All functions are pure / closed-form and deterministic. Use the
matching MCP wrappers in :mod:`mcp_ltspice.server` to drive them from
an agent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from numpy.typing import NDArray

# ---------------------------------------------------------------------------
# 1. SMPS Pi-section output filter
# ---------------------------------------------------------------------------


@dataclass
class PiFilterDesign:
    """Result of :func:`design_pi_output_filter`."""

    L_h: float
    C_in_f: float
    C_out_f: float
    f_resonance_hz: float
    attenuation_at_f_target_db: float
    attenuation_at_f_sw_db: float
    damping_resistor_advice: str
    notes: list[str] = field(default_factory=list)


def design_pi_output_filter(
    *,
    f_switching_hz: float,
    f_target_hz: float | None = None,
    attenuation_target_db: float = 40.0,
    i_out_a: float = 1.0,
    c_in_initial_f: float = 10e-6,
    cap_voltage_rating_v: float = 25.0,
    z0_load_ohm: float = 1.0,
) -> PiFilterDesign:
    """Size a Pi-section LC filter (C-L-C) for additional SMPS output
    ripple attenuation.

    The Pi filter is a 3rd-order LPF: ``C_in → L → C_out``. Its
    attenuation rises 60 dB/decade above the resonant frequency
    ``f_0 = 1 / (2π √(L · C_avg))`` where ``C_avg`` is the parallel
    combination of ``C_in`` and ``C_out``.

    Strategy:

    1. Take the converter's existing output cap as ``C_in`` (default 10 µF).
    2. Pick ``f_target_hz`` as the design point — by default,
       ``f_target_hz = f_switching_hz`` (the dominant ripple component).
       Override for harmonic-specific suppression (e.g., 5 × f_sw).
    3. Solve ``L`` so the chosen ``f_resonance`` is one decade below
       ``f_target``: ``f_0 = f_target / 10``. With ``C_in = C_out``
       initially equal, ``f_0 = 1/(2π√(L·C/2))`` ⇒ ``L = 2/((2π·f_0)²·C_in)``.
    4. Pick ``C_out`` to give the requested attenuation at ``f_target``:
       ``A_db ≈ 20·log10((f_target / f_0)²)`` for asymptotic 40 dB/decade.

    Parameters
    ----------
    f_switching_hz
        Converter switching frequency.
    f_target_hz
        Frequency at which ``attenuation_target_db`` must be met. Defaults
        to ``f_switching_hz`` (suppress fundamental ripple).
    attenuation_target_db
        Minimum attenuation at ``f_target_hz`` (positive dB).
    i_out_a
        DC output current — used to flag inductor saturation risk.
    c_in_initial_f
        Existing converter output cap that becomes the filter's input cap.
    cap_voltage_rating_v
        Working voltage rating constraint flagged in notes.
    z0_load_ohm
        Output load impedance (rough). Used for the damping recommendation.

    Returns
    -------
    PiFilterDesign

    Notes
    -----
    A bare Pi LC has very high Q at resonance — when the load is reactive,
    ringing can amplify supply-step disturbances. The
    ``damping_resistor_advice`` field describes a series-RC across
    ``C_out`` (typical R ≈ √(L/C_out), C_damp ≈ 5-10× C_out).
    """
    if f_switching_hz <= 0:
        raise ValueError("f_switching_hz must be > 0")
    if attenuation_target_db <= 0:
        raise ValueError("attenuation_target_db must be > 0")
    if c_in_initial_f <= 0:
        raise ValueError("c_in_initial_f must be > 0")

    f_target = f_target_hz if f_target_hz is not None else f_switching_hz
    if f_target <= 0:
        raise ValueError("f_target_hz must be > 0")

    # 60 dB/decade above f_0 → f_0 = f_target / 10^(A_db/60)
    decades_below_target = attenuation_target_db / 60.0
    f_resonance = f_target / (10.0**decades_below_target)
    omega_0 = 2.0 * math.pi * f_resonance

    # Initial: C_in == C_out → C_avg = C_in / 2
    L_h = 2.0 / (omega_0**2 * c_in_initial_f)
    c_out_f = c_in_initial_f  # symmetric Pi

    # Achieved attenuation at f_target (asymptotic 60 dB/dec for 3rd-order)
    octaves_above_f0 = math.log2(f_target / f_resonance)
    atten_at_target_db = 18.0 * octaves_above_f0  # 18 dB/octave = 60 dB/decade

    # Attenuation at the fundamental switching frequency
    if f_switching_hz > f_resonance:
        octaves_at_sw = math.log2(f_switching_hz / f_resonance)
        atten_at_sw_db = 18.0 * octaves_at_sw
    else:
        atten_at_sw_db = -18.0 * math.log2(f_resonance / f_switching_hz)

    # Damping advice
    z_filter = math.sqrt(L_h / c_out_f)  # characteristic impedance
    r_damp = z_filter * 1.5  # rule-of-thumb 1.5×Z_0 yields critical damping
    c_damp = 5.0 * c_out_f
    damping_advice = (
        f"Add a damping branch in parallel with C_out: R = {r_damp * 1000:.1f} mΩ "
        f"in series with C_damp = {c_damp * 1e6:.1f} µF. "
        f"Reduces resonance Q from open-Q to ~0.7 (critical) without DC dissipation."
    )

    notes: list[str] = []
    # Ripple current saturation check (assume 30 % I_L ripple at f_sw)
    delta_i = 0.3 * i_out_a
    i_peak = i_out_a + delta_i / 2
    notes.append(
        f"Inductor must be rated for I_DC = {i_out_a:.2f} A + ripple → I_peak ≈ {i_peak:.2f} A. "
        f"Pick a part with I_sat ≥ 1.5 × I_peak ≈ {1.5 * i_peak:.2f} A."
    )
    # Cap voltage rating
    notes.append(
        f"Caps see DC bus voltage; use ≥ {cap_voltage_rating_v:.0f} V rated parts. "
        f"For X7R / X5R MLCCs, derate for DC bias (typ. 50% capacitance loss at rated V)."
    )
    if f_resonance < f_switching_hz:
        notes.append(
            f"f_0 = {f_resonance / 1e3:.1f} kHz < f_sw = {f_switching_hz / 1e3:.1f} kHz "
            "as required for ripple suppression."
        )
    else:
        notes.append(
            "WARNING: f_0 ≥ f_sw — filter will not attenuate fundamental ripple. "
            "Reduce attenuation_target_db or increase c_in_initial_f."
        )

    return PiFilterDesign(
        L_h=L_h,
        C_in_f=c_in_initial_f,
        C_out_f=c_out_f,
        f_resonance_hz=f_resonance,
        attenuation_at_f_target_db=atten_at_target_db,
        attenuation_at_f_sw_db=atten_at_sw_db,
        damping_resistor_advice=damping_advice,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# 2. DM input EMI filter
# ---------------------------------------------------------------------------


@dataclass
class DmInputFilterDesign:
    """Result of :func:`design_dm_input_filter`."""

    L_h: float
    C_f: float
    f_corner_hz: float
    attenuation_at_f_sw_db: float
    damping_resistor_ohm: float
    damping_cap_f: float
    middlebrook_margin_db: float | None
    middlebrook_stable: bool | None
    notes: list[str] = field(default_factory=list)


def design_dm_input_filter(
    *,
    f_switching_hz: float,
    attenuation_target_db: float = 40.0,
    i_in_a: float = 1.0,
    converter_input_impedance_ohm: float = 1.0,
    lisn_impedance_ohm: float = 50.0,
    safety_factor: float = 6.0,
    c_initial_f: float = 4.7e-6,
) -> DmInputFilterDesign:
    """Size a 2nd-order LC differential-mode input EMI filter.

    The filter is a series ``L`` followed by shunt ``C`` to ground. Its
    -3 dB corner is ``f_c = 1/(2π √(LC))``; asymptotic attenuation above
    the corner rises at 40 dB/decade.

    The Middlebrook criterion checks that the filter's output impedance
    at the converter's resonant frequencies is significantly lower than
    the converter's input impedance — otherwise the filter destabilises
    the converter's control loop. We require
    ``|Z_out,filter| < |Z_in,converter| / safety_factor`` (default 6×,
    i.e. ~16 dB margin) at the filter resonance.

    Parameters
    ----------
    f_switching_hz
        Converter switching frequency (sets the design point for
        attenuation).
    attenuation_target_db
        Required attenuation at ``f_switching_hz``. Choose 30-50 dB for
        typical CISPR class B compliance with a single-stage filter.
    i_in_a
        DC input current — used to flag inductor saturation risk.
    converter_input_impedance_ohm
        Magnitude of the converter's negative-resistance input
        impedance at the loop crossover. For a buck converter at full
        load: ``R_in = V_in² / P_in``. Used for the Middlebrook check.
    lisn_impedance_ohm
        LISN source impedance presented to the filter (50 Ω typical).
        Affects the achieved attenuation but not the synthesis.
    safety_factor
        Middlebrook margin: filter's output impedance at resonance must
        be at least this factor below converter's input impedance.
    c_initial_f
        Filter cap initial guess. Increase to lower corner frequency.

    Returns
    -------
    DmInputFilterDesign

    Notes
    -----
    Without a damping branch, the filter has high Q at resonance which
    can ring under load steps. A damping cap is sized as
    ``C_d = 4 · C_filter`` with ``R_d = √(L/(4·C_filter))``, a standard
    series-RC damping recipe (Erickson & Maksimović, *Fundamentals of
    Power Electronics*, §10.4).
    """
    if f_switching_hz <= 0:
        raise ValueError("f_switching_hz must be > 0")
    if attenuation_target_db <= 0:
        raise ValueError("attenuation_target_db must be > 0")
    if c_initial_f <= 0:
        raise ValueError("c_initial_f must be > 0")
    if safety_factor <= 1:
        raise ValueError("safety_factor must be > 1")

    # 40 dB/dec → f_c = f_sw / 10^(A_db/40)
    decades_below_sw = attenuation_target_db / 40.0
    f_corner = f_switching_hz / (10.0**decades_below_sw)
    omega_c = 2.0 * math.pi * f_corner
    L_h = 1.0 / (omega_c**2 * c_initial_f)

    # Achieved attenuation at f_sw
    octaves_above_corner = math.log2(f_switching_hz / f_corner)
    atten_at_sw = 12.0 * octaves_above_corner  # 12 dB/octave = 40 dB/decade

    # Filter characteristic impedance (= |Z_out| at resonance with ideal source)
    z_filter = math.sqrt(L_h / c_initial_f)

    # Middlebrook check: |Z_out,filter,peak| ≤ |Z_in,converter| / safety_factor
    # |Z_out| at peak (if undamped) ≈ Z_filter × Q. For damped 2nd-order
    # (Q ≈ 0.7), peak |Z_out| ≈ Z_filter / √2.
    z_out_peak_damped = z_filter / math.sqrt(2.0)
    middlebrook_margin_db: float | None
    middlebrook_stable: bool | None
    if converter_input_impedance_ohm > 0:
        margin_lin = converter_input_impedance_ohm / z_out_peak_damped
        middlebrook_margin_db = 20.0 * math.log10(margin_lin)
        middlebrook_stable = margin_lin >= safety_factor
    else:
        middlebrook_margin_db = None
        middlebrook_stable = None

    # Damping branch (series RC parallel with C_filter)
    c_damp = 4.0 * c_initial_f
    r_damp = math.sqrt(L_h / (4.0 * c_initial_f))

    notes: list[str] = []
    notes.append(
        f"Filter corner f_c = {f_corner / 1e3:.1f} kHz; switching freq "
        f"{f_switching_hz / 1e3:.1f} kHz is {octaves_above_corner:.1f} octaves above. "
        f"Attenuation at f_sw ≈ {atten_at_sw:.1f} dB."
    )
    notes.append(
        f"Inductor must be rated for I_DC = {i_in_a:.2f} A and tolerate the "
        f"input ripple current. Choose I_sat ≥ {1.5 * i_in_a:.2f} A."
    )
    notes.append(
        f"Add damping branch: C_d = {c_damp * 1e6:.1f} µF in series with "
        f"R_d = {r_damp:.2f} Ω, in parallel with the main filter cap. "
        f"Reduces filter Q to ~0.7 for stable transient response."
    )
    if middlebrook_stable is False:
        notes.append(
            f"WARNING: Middlebrook margin only {middlebrook_margin_db:.1f} dB "
            f"(< target {20.0 * math.log10(safety_factor):.1f} dB). "
            f"Filter may destabilise the converter; add damping or increase L · C product."
        )
    elif middlebrook_stable is True:
        notes.append(
            f"Middlebrook margin: {middlebrook_margin_db:.1f} dB (≥ "
            f"{20.0 * math.log10(safety_factor):.1f} dB target) — filter / converter "
            f"interaction is stable."
        )

    # LISN impact note
    notes.append(
        f"LISN source impedance {lisn_impedance_ohm:.0f} Ω; filter's "
        f"actual attenuation depends on the impedance ratio at the "
        f"emissions test point (CISPR test setup)."
    )

    return DmInputFilterDesign(
        L_h=L_h,
        C_f=c_initial_f,
        f_corner_hz=f_corner,
        attenuation_at_f_sw_db=atten_at_sw,
        damping_resistor_ohm=r_damp,
        damping_cap_f=c_damp,
        middlebrook_margin_db=middlebrook_margin_db,
        middlebrook_stable=middlebrook_stable,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# 3. Conducted emissions LISN predictor with CISPR limits
# ---------------------------------------------------------------------------


# CISPR 22 / 32 conducted-emission limits for IT equipment, AVG / QP detector,
# 0.15 – 30 MHz. Class B (residential) is 6 dB tighter than Class A (industrial).
# Returned in dBµV referred to a 50 Ω LISN.
_CISPR_22_LIMITS_QP_DBUV: dict[str, list[tuple[float, float, float]]] = {
    # (f_low_hz, f_high_hz, limit_dbuv) — quasi-peak detector
    "class_a": [
        (0.15e6, 0.50e6, 79.0),  # 79 dBµV
        (0.50e6, 30.0e6, 73.0),  # 73 dBµV
    ],
    "class_b": [
        (0.15e6, 0.50e6, 66.0),  # 66 → 56 dBµV (decreasing log) but use upper bound
        (0.50e6, 5.00e6, 56.0),
        (5.00e6, 30.0e6, 60.0),
    ],
}

# Average detector limits (QP - 13 dB approximately for AVG)
_CISPR_22_LIMITS_AVG_DBUV: dict[str, list[tuple[float, float, float]]] = {
    "class_a": [
        (0.15e6, 0.50e6, 66.0),
        (0.50e6, 30.0e6, 60.0),
    ],
    "class_b": [
        (0.15e6, 0.50e6, 56.0),
        (0.50e6, 5.00e6, 46.0),
        (5.00e6, 30.0e6, 50.0),
    ],
}


def _cispr_limit_at(
    freq_hz: float,
    cls: Literal["class_a", "class_b"],
    detector: Literal["qp", "avg"],
) -> float | None:
    """Return CISPR 22/32 limit in dBµV at ``freq_hz``, or ``None`` outside 0.15–30 MHz."""
    table = _CISPR_22_LIMITS_QP_DBUV if detector == "qp" else _CISPR_22_LIMITS_AVG_DBUV
    if cls not in table:
        raise ValueError(f"Unknown class {cls!r}; expected 'class_a' or 'class_b'")
    for f_lo, f_hi, lim in table[cls]:
        if f_lo <= freq_hz < f_hi:
            return lim
    return None


@dataclass
class ConductedEmissionsPrediction:
    """Result of :func:`predict_conducted_emissions`."""

    freq_hz: NDArray[np.float64]
    emission_dbuv: NDArray[np.float64]
    cispr_class: str
    cispr_detector: str
    limit_dbuv: NDArray[np.float64]  # NaN where freq is outside CISPR range
    margin_db: NDArray[np.float64]  # limit - emission; positive = pass
    worst_margin_db: float
    worst_margin_freq_hz: float
    pass_status: bool
    n_harmonics: int
    notes: list[str] = field(default_factory=list)


def predict_conducted_emissions(
    *,
    f_switching_hz: float,
    duty_cycle: float = 0.5,
    switch_voltage_v: float,
    rise_time_s: float,
    n_harmonics: int = 100,
    filter_attenuation_db_at_f_sw: float = 0.0,
    filter_attenuation_slope_db_per_decade: float = 40.0,
    cispr_class: Literal["class_a", "class_b"] = "class_b",
    cispr_detector: Literal["qp", "avg"] = "qp",
) -> ConductedEmissionsPrediction:
    """Predict conducted-emission spectrum at the LISN port and compare to CISPR limits.

    Models the converter's switching node as a trapezoidal voltage waveform
    with amplitude ``switch_voltage_v``, duty cycle ``duty_cycle``, and
    edge transition time ``rise_time_s``. Computes the harmonic spectrum
    via the standard envelope:

        |V_n| = 2·V·D · |sinc(π·n·D)| · |sinc(π·n·f_sw·tr)|

    where ``n`` is the harmonic number, ``D`` is duty cycle, and ``tr``
    is rise time. The first sinc term encodes the duty cycle envelope
    (bandwidth ~ 1/(πD/f_sw)), the second the edge bandwidth
    (~ 1/(π·tr)).

    The DM input filter's attenuation is applied as a flat
    ``filter_attenuation_db_at_f_sw`` at ``f_switching_hz`` plus a
    ``filter_attenuation_slope_db_per_decade`` rolloff above. This
    captures the typical 40 dB/dec of a 2nd-order LC filter without
    requiring an explicit S-parameter file.

    Each harmonic frequency is compared to the CISPR 22/32 conducted
    limit (Class A or B, QP or AVG detector) referred to a 50 Ω LISN.

    Parameters
    ----------
    f_switching_hz
        Converter switching frequency.
    duty_cycle
        Converter duty cycle, 0 < D < 1.
    switch_voltage_v
        Peak-to-peak voltage swing on the switching node.
    rise_time_s
        Edge transition time (10–90 % typical). Faster edges → broader
        spectrum.
    n_harmonics
        Number of harmonics to evaluate (default 100; covers up to
        ~30 MHz at typical f_sw of 300 kHz).
    filter_attenuation_db_at_f_sw
        Flat attenuation provided by the DM input filter at
        ``f_switching_hz``. Default 0 (no filter — for un-filtered
        baseline). Pass the value from :func:`design_dm_input_filter`'s
        ``attenuation_at_f_sw_db``.
    filter_attenuation_slope_db_per_decade
        Filter rolloff above corner. 40 dB/dec for 2nd-order, 60 dB/dec
        for 3rd-order Pi.
    cispr_class
        ``"class_a"`` (industrial) or ``"class_b"`` (residential, 6 dB tighter).
    cispr_detector
        ``"qp"`` (quasi-peak) or ``"avg"`` (average; ~13 dB tighter).

    Returns
    -------
    ConductedEmissionsPrediction
        Per-harmonic frequency / emission / limit / margin arrays plus
        a pass/fail summary.
    """
    if not 0 < duty_cycle < 1:
        raise ValueError(f"duty_cycle must be in (0, 1), got {duty_cycle}")
    if rise_time_s <= 0:
        raise ValueError("rise_time_s must be > 0")
    if n_harmonics < 1:
        raise ValueError("n_harmonics must be ≥ 1")

    n_arr = np.arange(1, n_harmonics + 1, dtype=float)
    freq_hz = n_arr * f_switching_hz

    # Trapezoidal-pulse Fourier amplitude (V peak per harmonic, then dBµV at LISN)
    # Avoid divide-by-zero on sinc at integer arg
    arg1 = np.pi * n_arr * duty_cycle
    arg2 = np.pi * n_arr * f_switching_hz * rise_time_s

    sinc1 = np.where(np.abs(arg1) < 1e-12, 1.0, np.sin(arg1) / arg1)
    sinc2 = np.where(np.abs(arg2) < 1e-12, 1.0, np.sin(arg2) / arg2)

    v_n = 2.0 * switch_voltage_v * duty_cycle * np.abs(sinc1) * np.abs(sinc2)
    # Convert peak voltage to dBµV (V_peak = sqrt(2) * V_rms; dBµV uses RMS)
    v_n_rms = v_n / math.sqrt(2.0)
    emission_dbuv = 20.0 * np.log10(np.maximum(v_n_rms * 1e6, 1e-12))

    # Apply DM filter attenuation: flat at f_sw, then slope above
    decade_above_sw = np.log10(np.maximum(freq_hz / f_switching_hz, 1.0))
    filter_atten_db = (
        filter_attenuation_db_at_f_sw + decade_above_sw * filter_attenuation_slope_db_per_decade
    )
    emission_dbuv = emission_dbuv - filter_atten_db

    # CISPR limit per harmonic (NaN outside 0.15–30 MHz)
    limit_dbuv = np.array(
        [_cispr_limit_at(f, cispr_class, cispr_detector) or np.nan for f in freq_hz]
    )
    margin_db = limit_dbuv - emission_dbuv  # positive = pass

    # Worst margin within CISPR range
    valid = np.isfinite(margin_db)
    if valid.any():
        worst_idx_local = int(np.argmin(margin_db[valid]))
        valid_indices = np.where(valid)[0]
        worst_idx = int(valid_indices[worst_idx_local])
        worst_margin_db = float(margin_db[worst_idx])
        worst_margin_freq_hz = float(freq_hz[worst_idx])
        pass_status = bool(worst_margin_db > 0)
    else:
        worst_margin_db = float("nan")
        worst_margin_freq_hz = float("nan")
        pass_status = False

    notes: list[str] = []
    notes.append(
        f"Trapezoid model: V = {switch_voltage_v:.1f} V, D = {duty_cycle:.2f}, "
        f"tr = {rise_time_s * 1e9:.0f} ns. Envelope rolloffs at "
        f"f_dc = 1/(πD/f_sw) ≈ {f_switching_hz / (math.pi * duty_cycle) / 1e6:.1f} MHz "
        f"and f_tr = 1/(π·tr) ≈ {1.0 / (math.pi * rise_time_s) / 1e6:.1f} MHz."
    )
    notes.append(
        f"CISPR 22/32 {cispr_class.replace('_', ' ').upper()} {cispr_detector.upper()} "
        f"detector limits applied (0.15–30 MHz)."
    )
    if pass_status:
        notes.append(
            f"PASS: worst-case margin {worst_margin_db:.1f} dB at "
            f"{worst_margin_freq_hz / 1e6:.2f} MHz."
        )
    else:
        notes.append(
            f"FAIL: worst-case overshoot {-worst_margin_db:.1f} dB at "
            f"{worst_margin_freq_hz / 1e6:.2f} MHz. Improve filter attenuation, "
            f"slow edge rate, or raise switching frequency."
        )

    return ConductedEmissionsPrediction(
        freq_hz=freq_hz,
        emission_dbuv=emission_dbuv,
        cispr_class=cispr_class,
        cispr_detector=cispr_detector,
        limit_dbuv=limit_dbuv,
        margin_db=margin_db,
        worst_margin_db=worst_margin_db,
        worst_margin_freq_hz=worst_margin_freq_hz,
        pass_status=pass_status,
        n_harmonics=n_harmonics,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# 4. RC snubber design
# ---------------------------------------------------------------------------


@dataclass
class RcSnubberDesign:
    """Result of :func:`design_rc_snubber`."""

    R_ohm: float
    C_f: float
    f_ring_hz: float
    Q_undamped: float
    damping_factor: float
    dissipation_w: float
    notes: list[str] = field(default_factory=list)


def design_rc_snubber(
    *,
    parasitic_l_h: float,
    coss_f: float,
    peak_voltage_v: float,
    f_switching_hz: float,
    target_damping: float = 0.7,
) -> RcSnubberDesign:
    """Design an RC snubber that damps switch-node ringing.

    The switch node forms an LC tank with parasitic loop inductance
    ``L_par`` (PCB trace + lead) and the switching device's
    ``C_oss + C_diode`` (output capacitance plus reverse-recovery
    capacitance). Without damping, the ring frequency is

        f_ring = 1 / (2π √(L_par · C_oss))

    and the unloaded Q is high (~10-50), causing voltage overshoot and
    EMI in the 10-100 MHz range.

    The snubber is a series RC across the switch node. Standard recipe
    (RDL 1989, *Power Electronics Handbook*, Erickson §10.5):

    - C_snubber = ``C_oss`` (so the snubber sees half the energy)
    - R_snubber = ``√(L_par / C_oss)`` × (1 / (2 · damping_factor))

    With ``damping_factor = 0.7`` (critical-ish), the snubber R brings
    the loop Q to ≈ 1, eliminating the overshoot.

    Per-cycle dissipation in the snubber R:

        P_loss = ½ · C_snubber · V_peak² · f_sw

    Parameters
    ----------
    parasitic_l_h
        Loop inductance (PCB trace + lead + via). Typical 3-30 nH for
        a well-laid-out switch node.
    coss_f
        Output capacitance of the switching device + diode. Look up
        ``C_oss`` in the FET datasheet at the operating drain-source voltage.
    peak_voltage_v
        Peak switch-node voltage (V_in for buck high-side switch on
        turn-off). Used for dissipation calc.
    f_switching_hz
        Switching frequency.
    target_damping
        Damping factor. 0.7 = critical-ish (no overshoot, no ringing);
        1.0 = critically damped (slowest transition); < 0.5 = under-damped
        (some ringing remaining).

    Returns
    -------
    RcSnubberDesign
    """
    if parasitic_l_h <= 0 or coss_f <= 0:
        raise ValueError("parasitic_l_h and coss_f must be > 0")
    if peak_voltage_v < 0:
        raise ValueError("peak_voltage_v must be ≥ 0")
    if f_switching_hz <= 0:
        raise ValueError("f_switching_hz must be > 0")
    if not 0 < target_damping <= 1.0:
        raise ValueError("target_damping must be in (0, 1]")

    f_ring = 1.0 / (2.0 * math.pi * math.sqrt(parasitic_l_h * coss_f))
    z_ring = math.sqrt(parasitic_l_h / coss_f)
    # Snubber R such that ζ = R / (2 · Z_ring) when C_s = C_oss
    r_snubber = z_ring * 2.0 * target_damping
    c_snubber = coss_f
    q_undamped = 50.0  # ballpark; depends on layout
    dissipation = 0.5 * c_snubber * peak_voltage_v**2 * f_switching_hz

    notes: list[str] = []
    notes.append(
        f"Switch-node ring f_ring = {f_ring / 1e6:.1f} MHz, characteristic "
        f"impedance Z_ring = {z_ring:.2f} Ω."
    )
    notes.append(
        f"Snubber R = {r_snubber:.2f} Ω, C = {c_snubber * 1e9:.2f} nF "
        f"(target damping ζ = {target_damping:.2f})."
    )
    notes.append(
        f"Dissipation: {dissipation * 1e3:.2f} mW at {f_switching_hz / 1e3:.0f} kHz. "
        f"Use a film cap (NPO/C0G or X7R rated for full V_peak) and a small "
        f"low-inductance resistor (0402/0603 thick film)."
    )
    if r_snubber > 100:
        notes.append(
            "Snubber R is high; verify the snubber doesn't significantly slow the "
            "switch transition (longer t_r) — that would worsen efficiency."
        )

    return RcSnubberDesign(
        R_ohm=r_snubber,
        C_f=c_snubber,
        f_ring_hz=f_ring,
        Q_undamped=q_undamped,
        damping_factor=target_damping,
        dissipation_w=dissipation,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# 5. Common-mode choke selection
# ---------------------------------------------------------------------------


@dataclass
class CmChoke:
    """One entry in the curated CM choke catalogue."""

    part_number: str
    L_cm_h: float  # common-mode inductance per winding
    L_dm_leakage_h: float  # leakage inductance (DM)
    i_dc_max_a: float
    z_cm_at_1mhz_ohm: float
    package: str


# Curated CM choke catalogue — a representative sample of widely-used parts.
# Values from public datasheets (Würth WE-CMB, TDK ZJYS series).
_CM_CHOKE_CATALOGUE: list[CmChoke] = [
    CmChoke("WE-CMB 744232xxx", 11e-3, 22e-6, 0.4, 5000.0, "0805"),
    CmChoke("WE-CMB 744233xxx", 22e-3, 36e-6, 0.4, 9500.0, "0805"),
    CmChoke("WE-CMB 744271xxx", 100e-6, 1.0e-6, 1.5, 800.0, "0603"),
    CmChoke("WE-CMB 744272xxx", 470e-6, 4.0e-6, 1.5, 3500.0, "0603"),
    CmChoke("TDK ZJYS series-2", 2.2e-3, 5.0e-6, 1.0, 1500.0, "1812"),
    CmChoke("TDK ZJYS series-10", 10e-3, 22e-6, 0.5, 4500.0, "1812"),
    CmChoke("TDK ACT45B-220", 22e-6, 0.4e-6, 4.0, 200.0, "1210"),
    CmChoke("Murata DLW21SN670HQ2", 67e-6, 1.5e-6, 2.4, 600.0, "0805"),
]


@dataclass
class CmChokeRecommendation:
    """Result of :func:`design_cm_choke`."""

    chosen: CmChoke | None
    candidates: list[CmChoke]
    target_z_cm_ohm: float
    target_freq_hz: float
    notes: list[str] = field(default_factory=list)


def design_cm_choke(
    *,
    i_dc_a: float,
    target_z_cm_ohm: float,
    target_freq_hz: float = 1e6,
    max_dm_leakage_h: float = 50e-6,
) -> CmChokeRecommendation:
    """Pick a common-mode choke from a curated catalogue.

    Filtering criteria:

    - ``i_dc_a`` ≤ catalogue ``i_dc_max_a`` (current rating, with margin)
    - ``z_cm_at_1mhz_ohm`` ≥ ``target_z_cm_ohm`` (CM impedance at 1 MHz;
      scaled for ``target_freq_hz`` assuming 20 dB/decade rise above the
      catalogue's listed point)
    - ``L_dm_leakage_h`` ≤ ``max_dm_leakage_h`` (so the choke doesn't add
      excessive DM impedance to the supply rail)

    Returns the highest-margin candidate plus the full filtered list so
    the engineer can compare.

    Parameters
    ----------
    i_dc_a
        DC current the choke must carry without saturation.
    target_z_cm_ohm
        Required CM impedance at ``target_freq_hz``.
    target_freq_hz
        Frequency at which ``target_z_cm_ohm`` is measured. Catalogue
        impedance values are at 1 MHz; we scale linearly with frequency
        (valid below the choke's self-resonance — typical 10–100 MHz).
    max_dm_leakage_h
        Cap on DM leakage inductance. Too-high leakage adds DM
        impedance which can interact with downstream filtering.

    Returns
    -------
    CmChokeRecommendation
    """
    if i_dc_a < 0:
        raise ValueError("i_dc_a must be ≥ 0")
    if target_z_cm_ohm <= 0:
        raise ValueError("target_z_cm_ohm must be > 0")
    if target_freq_hz <= 0:
        raise ValueError("target_freq_hz must be > 0")

    notes: list[str] = []
    notes.append(
        f"Looking for CM choke that delivers {target_z_cm_ohm:.0f} Ω at "
        f"{target_freq_hz / 1e6:.2f} MHz with I_DC ≤ {i_dc_a:.2f} A."
    )

    # Scale the catalogue's 1-MHz Z_cm to the target frequency.
    # Below SRF, |Z_cm| ≈ ω × L_cm, so it rises 20 dB/dec.
    freq_scale = target_freq_hz / 1e6
    candidates: list[CmChoke] = []
    for choke in _CM_CHOKE_CATALOGUE:
        if choke.i_dc_max_a < i_dc_a * 1.2:  # 20 % margin
            continue
        z_at_target = choke.z_cm_at_1mhz_ohm * freq_scale
        if z_at_target < target_z_cm_ohm:
            continue
        if choke.L_dm_leakage_h > max_dm_leakage_h:
            continue
        candidates.append(choke)

    if not candidates:
        notes.append(
            "No catalogue part meets all three criteria. Either relax "
            "max_dm_leakage_h, choose a higher-current chassis-mount choke, "
            "or accept lower Z_cm (use two cascaded chokes)."
        )
        return CmChokeRecommendation(
            chosen=None,
            candidates=[],
            target_z_cm_ohm=target_z_cm_ohm,
            target_freq_hz=target_freq_hz,
            notes=notes,
        )

    # Rank by CM-impedance margin × current margin / leakage penalty
    def _score(c: CmChoke) -> float:
        z_margin = (c.z_cm_at_1mhz_ohm * freq_scale) / target_z_cm_ohm
        i_margin = c.i_dc_max_a / max(i_dc_a, 1e-3)
        leak_pen = max(c.L_dm_leakage_h / 1e-6, 0.1)  # leakage in µH, lower is better
        return z_margin * i_margin / leak_pen

    candidates_ranked = sorted(candidates, key=_score, reverse=True)
    chosen = candidates_ranked[0]
    notes.append(
        f"Chosen: {chosen.part_number} — L_CM = {chosen.L_cm_h * 1e6:.1f} µH, "
        f"L_DM(leakage) = {chosen.L_dm_leakage_h * 1e6:.1f} µH, "
        f"I_DC = {chosen.i_dc_max_a:.1f} A, Z_CM at {target_freq_hz / 1e6:.1f} MHz "
        f"≈ {chosen.z_cm_at_1mhz_ohm * freq_scale:.0f} Ω."
    )
    if len(candidates_ranked) > 1:
        notes.append("Alternatives: " + ", ".join(c.part_number for c in candidates_ranked[1:5]))

    return CmChokeRecommendation(
        chosen=chosen,
        candidates=candidates_ranked,
        target_z_cm_ohm=target_z_cm_ohm,
        target_freq_hz=target_freq_hz,
        notes=notes,
    )


__all__ = [
    "CmChoke",
    "CmChokeRecommendation",
    "ConductedEmissionsPrediction",
    "DmInputFilterDesign",
    "PiFilterDesign",
    "RcSnubberDesign",
    "design_cm_choke",
    "design_dm_input_filter",
    "design_pi_output_filter",
    "design_rc_snubber",
    "predict_conducted_emissions",
]
