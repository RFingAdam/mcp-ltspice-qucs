"""Digital-to-analog crosstalk + supply-noise injection.

When a digital signal switches near an analog rail or signal trace, two
mechanisms inject noise:

1. **Capacitive crosstalk** through coupling capacitance between traces
   (proportional to dV/dt of the aggressor and the C_coupling).

2. **Supply-noise injection**: digital switching current pulled through
   the supply network, creating IR + L(di/dt) drops on the rail that
   propagates everywhere.

These are hand-analytical estimates — useful for early architecture
decisions ("can I run this ADC and that DSP off the same supply?").
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass
class DigitalAggressor:
    """One switching digital signal."""

    name: str
    swing_v: float  # peak-to-peak voltage swing (e.g. 1.8 for 1.8V CMOS)
    rise_time_ns: float  # 0-100% (or 10-90%, doesn't matter much for first-order)
    switching_freq_mhz: float  # typical activity rate
    capacitance_load_pf: float  # gate + wire capacitance the driver charges


def estimate_digital_to_analog_crosstalk(
    aggressor: DigitalAggressor,
    coupling_capacitance_ff: float,  # mutual capacitance to victim (femtoFarads)
    victim_impedance_ohm: float,
) -> dict[str, Any]:
    """Estimate the noise voltage induced on a high-Z analog node by a
    nearby switching digital signal.

    First-order: V_noise = C_couple · dV/dt · Z_victim
    For a step at the aggressor: dV/dt = swing / rise_time
    """
    dv_dt_v_per_s = aggressor.swing_v / (aggressor.rise_time_ns * 1e-9)
    c_couple_f = coupling_capacitance_ff * 1e-15
    # Coupled current pulse
    i_coupled_a = c_couple_f * dv_dt_v_per_s
    # Noise voltage on victim
    v_noise_pk_v = i_coupled_a * victim_impedance_ohm
    return {
        "aggressor_dvdt_v_per_us": dv_dt_v_per_s / 1e6,
        "coupled_current_ua": i_coupled_a * 1e6,
        "victim_noise_peak_uv": v_noise_pk_v * 1e6,
        "victim_noise_peak_dbm": 20 * math.log10(max(v_noise_pk_v / 1.0, 1e-12)),
        "concern_level": _classify_noise(v_noise_pk_v, victim_impedance_ohm),
    }


def _classify_noise(v_noise_v: float, z_victim: float) -> str:
    # Rule of thumb: if induced noise > LSB of a 12-bit ADC at full
    # scale (which is V_ref / 4096 ~ 1 mV for 3.3V system), it's a problem.
    if v_noise_v > 1e-3:
        return "critical"
    if v_noise_v > 1e-4:
        return "high"
    if v_noise_v > 1e-5:
        return "medium"
    return "low"


def estimate_supply_noise_injection(
    aggressor: DigitalAggressor,
    supply_inductance_nh: float = 5.0,  # typical PCB power-net partial L
    supply_resistance_mohm: float = 10.0,  # PDN resistance at switching freq
    n_simultaneous_switches: int = 1,
) -> dict[str, Any]:
    """Estimate the V_droop on the supply rail due to digital switching.

    Charge per switch: Q = C_load · ΔV
    Switching current: I_switch ≈ C_load · ΔV / t_rise · n_simultaneous
    Voltage droop:    V_droop = I·R + L·(dI/dt)
    """
    n = n_simultaneous_switches
    c_load_f = aggressor.capacitance_load_pf * 1e-12 * n
    dt_s = aggressor.rise_time_ns * 1e-9
    delta_v = aggressor.swing_v

    # Peak switching current
    i_peak_a = c_load_f * delta_v / dt_s
    di_dt = i_peak_a / dt_s

    v_droop_resistive = i_peak_a * supply_resistance_mohm * 1e-3
    v_droop_inductive = supply_inductance_nh * 1e-9 * di_dt
    v_droop_total = v_droop_resistive + v_droop_inductive

    notes = []
    if v_droop_total > 0.05 * delta_v:
        notes.append(
            f"V_droop {v_droop_total * 1000:.1f} mV exceeds 5% of supply — "
            f"add bulk cap or reduce L by lowering loop area on PDN."
        )
    if v_droop_inductive > v_droop_resistive * 2:
        notes.append(
            "Inductive droop dominates — reduce supply inductance "
            "(more vias to plane, shorter traces) before reducing R."
        )

    return {
        "i_switch_peak_ma": i_peak_a * 1000,
        "di_dt_a_per_us": di_dt / 1e6,
        "v_droop_resistive_mv": v_droop_resistive * 1000,
        "v_droop_inductive_mv": v_droop_inductive * 1000,
        "v_droop_total_mv": v_droop_total * 1000,
        "v_droop_pct_of_swing": 100 * v_droop_total / delta_v,
        "notes": notes,
    }
