"""Tests for SMPS EMC pre-compliance tools (power/emc.py).

Five new tools that fill the gap between SMPS sizing (buck/boost/LDO)
and a real product passing conducted-emissions:

- design_pi_output_filter (Pi LC output filter)
- design_dm_input_filter (DM LC input filter, Middlebrook stable)
- predict_conducted_emissions (LISN spectrum + CISPR 22/32 limits)
- design_rc_snubber (switch-node ringing damper)
- design_cm_choke (catalogue selection)
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from mcp_ltspice.power.emc import (
    design_cm_choke,
    design_dm_input_filter,
    design_pi_output_filter,
    design_rc_snubber,
    predict_conducted_emissions,
)

# ---------------------------------------------------------------------------
# 1. Pi output filter
# ---------------------------------------------------------------------------


class TestPiOutputFilter:
    def test_basic_returns_sensible_values(self):
        d = design_pi_output_filter(
            f_switching_hz=500e3, attenuation_target_db=40, c_in_initial_f=10e-6
        )
        assert d.L_h > 0
        assert d.C_in_f == 10e-6
        assert d.C_out_f == 10e-6
        assert d.f_resonance_hz < 500e3, "Resonance must be below switching freq"

    def test_attenuation_at_target_meets_spec(self):
        for atten in [30, 40, 50, 60]:
            d = design_pi_output_filter(
                f_switching_hz=500e3, attenuation_target_db=atten, c_in_initial_f=10e-6
            )
            # Target is f_sw by default; achieved should match (60 dB/dec)
            assert d.attenuation_at_f_target_db == pytest.approx(atten, rel=0.01)

    def test_resonance_scales_with_target_attenuation(self):
        """Higher attenuation target → lower resonance (further below f_target)."""
        d_30 = design_pi_output_filter(f_switching_hz=500e3, attenuation_target_db=30)
        d_60 = design_pi_output_filter(f_switching_hz=500e3, attenuation_target_db=60)
        assert d_60.f_resonance_hz < d_30.f_resonance_hz

    def test_invalid_input_raises(self):
        with pytest.raises(ValueError):
            design_pi_output_filter(f_switching_hz=0, attenuation_target_db=40)
        with pytest.raises(ValueError):
            design_pi_output_filter(f_switching_hz=500e3, attenuation_target_db=-1)

    def test_resonant_freq_consistent_with_lc(self):
        d = design_pi_output_filter(
            f_switching_hz=500e3, attenuation_target_db=40, c_in_initial_f=10e-6
        )
        # f_0 = 1/(2π√(L · C/2)) for symmetric Pi
        c_avg = (d.C_in_f * d.C_out_f) / (d.C_in_f + d.C_out_f)
        f_check = 1.0 / (2 * math.pi * math.sqrt(d.L_h * c_avg))
        assert d.f_resonance_hz == pytest.approx(f_check, rel=1e-6)

    def test_damping_advice_has_substance(self):
        d = design_pi_output_filter(f_switching_hz=500e3, attenuation_target_db=40)
        assert "C_damp" in d.damping_resistor_advice
        assert "R" in d.damping_resistor_advice


# ---------------------------------------------------------------------------
# 2. DM input filter
# ---------------------------------------------------------------------------


class TestDmInputFilter:
    def test_basic_returns_sensible_values(self):
        d = design_dm_input_filter(
            f_switching_hz=500e3, attenuation_target_db=40, c_initial_f=4.7e-6
        )
        assert d.L_h > 0
        assert d.C_f == 4.7e-6
        assert d.f_corner_hz < 500e3, "Corner must be below switching freq"

    def test_corner_freq_scales_with_attenuation(self):
        d_40 = design_dm_input_filter(f_switching_hz=500e3, attenuation_target_db=40)
        d_60 = design_dm_input_filter(f_switching_hz=500e3, attenuation_target_db=60)
        assert d_60.f_corner_hz < d_40.f_corner_hz

    def test_lc_product_matches_corner_freq(self):
        d = design_dm_input_filter(
            f_switching_hz=500e3, attenuation_target_db=40, c_initial_f=4.7e-6
        )
        f_check = 1.0 / (2 * math.pi * math.sqrt(d.L_h * d.C_f))
        assert d.f_corner_hz == pytest.approx(f_check, rel=1e-6)

    def test_middlebrook_stable_when_converter_z_high(self):
        """High converter input Z → easy stability."""
        d = design_dm_input_filter(
            f_switching_hz=500e3,
            attenuation_target_db=40,
            converter_input_impedance_ohm=100.0,  # high
            safety_factor=6.0,
        )
        assert d.middlebrook_stable is True
        assert d.middlebrook_margin_db is not None
        assert d.middlebrook_margin_db >= 20.0 * math.log10(6.0) - 0.1

    def test_middlebrook_warns_when_converter_z_low(self):
        """Low converter Z (high-current rail) → Middlebrook can fail."""
        d = design_dm_input_filter(
            f_switching_hz=500e3,
            attenuation_target_db=80,  # huge filter, big L/C → high Z_filter
            converter_input_impedance_ohm=0.5,  # very low
            safety_factor=6.0,
        )
        # Either fails or warning surfaces
        if d.middlebrook_stable is False:
            assert any("WARNING" in n for n in d.notes)

    def test_damping_branch_sized(self):
        d = design_dm_input_filter(
            f_switching_hz=500e3, attenuation_target_db=40, c_initial_f=4.7e-6
        )
        assert d.damping_cap_f == pytest.approx(4 * d.C_f)
        assert d.damping_resistor_ohm > 0


# ---------------------------------------------------------------------------
# 3. Conducted emissions predictor
# ---------------------------------------------------------------------------


class TestConductedEmissions:
    def test_basic_returns_arrays_of_correct_shape(self):
        r = predict_conducted_emissions(
            f_switching_hz=500e3,
            switch_voltage_v=12.0,
            rise_time_s=20e-9,
            n_harmonics=50,
        )
        assert r.freq_hz.shape == (50,)
        assert r.emission_dbuv.shape == (50,)
        assert r.limit_dbuv.shape == (50,)
        assert r.margin_db.shape == (50,)

    def test_pass_with_aggressive_filter(self):
        """A large filter rolloff brings the spectrum below CISPR limits."""
        r = predict_conducted_emissions(
            f_switching_hz=500e3,
            switch_voltage_v=12.0,
            rise_time_s=20e-9,
            n_harmonics=100,
            filter_attenuation_db_at_f_sw=80,
            filter_attenuation_slope_db_per_decade=60,
        )
        assert r.pass_status is True
        assert r.worst_margin_db > 0

    def test_fail_with_no_filter(self):
        """Bare unfiltered SMPS will fail Class B at audio harmonics."""
        r = predict_conducted_emissions(
            f_switching_hz=500e3,
            switch_voltage_v=12.0,
            rise_time_s=20e-9,
            n_harmonics=100,
            filter_attenuation_db_at_f_sw=0.0,
        )
        assert r.pass_status is False
        assert r.worst_margin_db < 0

    def test_class_a_easier_than_class_b(self):
        """Class A is 6 dB looser; same emissions should have higher margin."""
        kwargs = {
            "f_switching_hz": 500e3,
            "switch_voltage_v": 12.0,
            "rise_time_s": 20e-9,
            "n_harmonics": 100,
            "filter_attenuation_db_at_f_sw": 50,
        }
        r_a = predict_conducted_emissions(**kwargs, cispr_class="class_a")
        r_b = predict_conducted_emissions(**kwargs, cispr_class="class_b")
        assert r_a.worst_margin_db > r_b.worst_margin_db

    def test_avg_detector_tighter_than_qp(self):
        """AVG limits ~10-13 dB tighter than QP."""
        kwargs = {
            "f_switching_hz": 500e3,
            "switch_voltage_v": 12.0,
            "rise_time_s": 20e-9,
            "n_harmonics": 100,
            "filter_attenuation_db_at_f_sw": 50,
        }
        r_qp = predict_conducted_emissions(**kwargs, cispr_detector="qp")
        r_avg = predict_conducted_emissions(**kwargs, cispr_detector="avg")
        assert r_qp.worst_margin_db > r_avg.worst_margin_db

    def test_faster_edges_more_emissions(self):
        """Faster rise time → broader spectrum → worse high-freq margin."""
        slow = predict_conducted_emissions(
            f_switching_hz=500e3,
            switch_voltage_v=12.0,
            rise_time_s=200e-9,
            n_harmonics=100,
            filter_attenuation_db_at_f_sw=50,
        )
        fast = predict_conducted_emissions(
            f_switching_hz=500e3,
            switch_voltage_v=12.0,
            rise_time_s=10e-9,
            n_harmonics=100,
            filter_attenuation_db_at_f_sw=50,
        )
        # Fast edges have more energy at HF; emission_dbuv at the highest harmonic
        # should be greater for the fast case.
        assert fast.emission_dbuv[-1] > slow.emission_dbuv[-1]

    def test_invalid_inputs_raise(self):
        with pytest.raises(ValueError):
            predict_conducted_emissions(
                f_switching_hz=500e3, switch_voltage_v=12.0, rise_time_s=20e-9, duty_cycle=1.5
            )
        with pytest.raises(ValueError):
            predict_conducted_emissions(f_switching_hz=500e3, switch_voltage_v=12.0, rise_time_s=-1)

    def test_limits_outside_cispr_range_are_nan(self):
        """Harmonics below 150 kHz or above 30 MHz have no CISPR limit (NaN)."""
        # f_sw = 50 kHz → 1st harmonic at 50 kHz, below 150 kHz CISPR start
        r = predict_conducted_emissions(
            f_switching_hz=50e3, switch_voltage_v=12.0, rise_time_s=20e-9, n_harmonics=2
        )
        assert np.isnan(r.limit_dbuv[0])  # 50 kHz — below 150 kHz


# ---------------------------------------------------------------------------
# 4. RC snubber
# ---------------------------------------------------------------------------


class TestRcSnubber:
    def test_basic_returns_sensible_values(self):
        d = design_rc_snubber(
            parasitic_l_h=15e-9, coss_f=200e-12, peak_voltage_v=24, f_switching_hz=500e3
        )
        assert d.R_ohm > 0
        assert d.C_f == 200e-12  # equals C_oss by recipe
        assert d.f_ring_hz > 1e6  # typical switch ring is in MHz
        assert d.dissipation_w > 0

    def test_ring_freq_matches_lc(self):
        d = design_rc_snubber(
            parasitic_l_h=15e-9, coss_f=200e-12, peak_voltage_v=24, f_switching_hz=500e3
        )
        f_check = 1.0 / (2 * math.pi * math.sqrt(15e-9 * 200e-12))
        assert d.f_ring_hz == pytest.approx(f_check, rel=1e-6)

    def test_damping_factor_drives_resistor(self):
        d_low = design_rc_snubber(
            parasitic_l_h=15e-9,
            coss_f=200e-12,
            peak_voltage_v=24,
            f_switching_hz=500e3,
            target_damping=0.3,
        )
        d_high = design_rc_snubber(
            parasitic_l_h=15e-9,
            coss_f=200e-12,
            peak_voltage_v=24,
            f_switching_hz=500e3,
            target_damping=1.0,
        )
        assert d_high.R_ohm > d_low.R_ohm

    def test_dissipation_scales_with_freq(self):
        d_low = design_rc_snubber(
            parasitic_l_h=15e-9, coss_f=200e-12, peak_voltage_v=24, f_switching_hz=100e3
        )
        d_high = design_rc_snubber(
            parasitic_l_h=15e-9, coss_f=200e-12, peak_voltage_v=24, f_switching_hz=1e6
        )
        # 10× freq → 10× dissipation
        assert d_high.dissipation_w == pytest.approx(d_low.dissipation_w * 10.0, rel=1e-6)

    def test_dissipation_scales_with_voltage_squared(self):
        d_low = design_rc_snubber(
            parasitic_l_h=15e-9, coss_f=200e-12, peak_voltage_v=12, f_switching_hz=500e3
        )
        d_high = design_rc_snubber(
            parasitic_l_h=15e-9, coss_f=200e-12, peak_voltage_v=24, f_switching_hz=500e3
        )
        # 2× V → 4× P
        assert d_high.dissipation_w == pytest.approx(d_low.dissipation_w * 4.0, rel=1e-6)

    def test_invalid_inputs_raise(self):
        with pytest.raises(ValueError):
            design_rc_snubber(
                parasitic_l_h=0, coss_f=200e-12, peak_voltage_v=24, f_switching_hz=500e3
            )
        with pytest.raises(ValueError):
            design_rc_snubber(
                parasitic_l_h=15e-9,
                coss_f=200e-12,
                peak_voltage_v=24,
                f_switching_hz=500e3,
                target_damping=2.0,
            )


# ---------------------------------------------------------------------------
# 5. CM choke selection
# ---------------------------------------------------------------------------


class TestCmChoke:
    def test_finds_part_for_modest_target(self):
        """Most realistic targets should find at least one candidate."""
        r = design_cm_choke(i_dc_a=0.5, target_z_cm_ohm=2000, target_freq_hz=1e6)
        assert r.chosen is not None
        assert r.chosen.i_dc_max_a >= 0.5
        # Z_cm at target freq must meet target
        z_at_target = r.chosen.z_cm_at_1mhz_ohm * (r.target_freq_hz / 1e6)
        assert z_at_target >= 2000

    def test_returns_none_for_impossible_target(self):
        """Unreachable target should return chosen=None."""
        r = design_cm_choke(i_dc_a=20.0, target_z_cm_ohm=100000, target_freq_hz=1e6)
        assert r.chosen is None
        assert r.candidates == []

    def test_high_current_filters_out_low_rated_parts(self):
        r_low = design_cm_choke(i_dc_a=0.1, target_z_cm_ohm=500, target_freq_hz=1e6)
        r_high = design_cm_choke(i_dc_a=3.0, target_z_cm_ohm=500, target_freq_hz=1e6)
        # High-current call must have ≤ low-current candidate count
        assert len(r_high.candidates) <= len(r_low.candidates)

    def test_leakage_bound_filters_high_leakage_parts(self):
        r_lax = design_cm_choke(i_dc_a=0.5, target_z_cm_ohm=500, max_dm_leakage_h=100e-6)
        r_strict = design_cm_choke(i_dc_a=0.5, target_z_cm_ohm=500, max_dm_leakage_h=2e-6)
        # Strict leakage cap must reduce candidate count
        assert len(r_strict.candidates) <= len(r_lax.candidates)

    def test_invalid_inputs_raise(self):
        with pytest.raises(ValueError):
            design_cm_choke(i_dc_a=-0.5, target_z_cm_ohm=500)
        with pytest.raises(ValueError):
            design_cm_choke(i_dc_a=0.5, target_z_cm_ohm=0)
