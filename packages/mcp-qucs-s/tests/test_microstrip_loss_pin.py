"""Numerical pins for microstrip dielectric + conductor loss.

The Pozar §3.8.1 dielectric-loss formula and the standard skin-effect
conductor-loss formula were never pinned before v0.3.0. A factor-of-
8.686 (Np/m → dB/m) slip or a missing ``√ε_eff`` would silently
corrupt every loss budget that uses :func:`analyze_microstrip`.

Strategy: rather than pinning against a single textbook example
(values vary across editions / errata), pin **scaling laws** the
formulas must satisfy. Bugs that violate these are caught
unambiguously; bugs that produce slightly-wrong absolute values are
caught by the order-of-magnitude guardrail.
"""

from __future__ import annotations

import math

import pytest

from mcp_qucs_s.microstrip import Substrate, analyze_microstrip


def _setup_fr4_at(freq_hz: float, tan_d: float = 0.02):
    sub = Substrate(er=4.2, h_mm=1.58, t_um=17.4, tan_d=tan_d)
    return sub, 3.05, freq_hz  # ~50 Ω line on 60 mil FR-4


class TestMicrostripLossScalingLaws:
    """Properties any correct dielectric / conductor loss formula obeys.
    Violations indicate real bugs (wrong factors, sign errors, missing
    terms)."""

    def test_dielectric_loss_linear_in_tan_d(self):
        """α_d ∝ tan_d. Doubling loss tangent must double α_d."""
        sub_low, w, f = _setup_fr4_at(10e9, tan_d=0.01)
        sub_high, _, _ = _setup_fr4_at(10e9, tan_d=0.02)
        ad_low = analyze_microstrip(w, sub_low, freq_hz=f)["alpha_d_db_per_mm"]
        ad_high = analyze_microstrip(w, sub_high, freq_hz=f)["alpha_d_db_per_mm"]
        assert ad_high == pytest.approx(2.0 * ad_low, rel=1e-9)

    def test_dielectric_loss_linear_in_frequency(self):
        """α_d ∝ k₀ ∝ f. Doubling frequency must double α_d (in dB/m)."""
        sub, w, f1 = _setup_fr4_at(5e9)
        ad_5 = analyze_microstrip(w, sub, freq_hz=f1)["alpha_d_db_per_mm"]
        ad_10 = analyze_microstrip(w, sub, freq_hz=2 * f1)["alpha_d_db_per_mm"]
        assert ad_10 == pytest.approx(2.0 * ad_5, rel=1e-3)

    def test_conductor_loss_sqrt_frequency(self):
        """α_c ∝ √f via skin-effect surface resistance.
        Quadrupling frequency must double α_c."""
        sub, w, f1 = _setup_fr4_at(2.5e9)
        ac_low = analyze_microstrip(w, sub, freq_hz=f1)["alpha_c_db_per_mm"]
        ac_4x = analyze_microstrip(w, sub, freq_hz=4 * f1)["alpha_c_db_per_mm"]
        assert ac_4x == pytest.approx(2.0 * ac_low, rel=1e-3)

    def test_conductor_loss_inverse_conductivity_sqrt(self):
        """α_c ∝ 1/√σ. Aluminium (σ ≈ 3.5e7) lossier than copper (5.8e7)
        by factor √(5.8/3.5) ≈ 1.287."""
        sub, w, f = _setup_fr4_at(5e9)
        ac_cu = analyze_microstrip(w, sub, freq_hz=f, sigma_s_per_m=5.8e7)["alpha_c_db_per_mm"]
        ac_al = analyze_microstrip(w, sub, freq_hz=f, sigma_s_per_m=3.5e7)["alpha_c_db_per_mm"]
        ratio = ac_al / ac_cu
        expected = math.sqrt(5.8e7 / 3.5e7)
        assert ratio == pytest.approx(expected, rel=1e-3)

    def test_dielectric_loss_zero_when_lossless(self):
        """Lossless substrate (tan_d = 0) → α_d = 0 exactly."""
        sub = Substrate(er=4.2, h_mm=1.58, t_um=17.4, tan_d=0.0)
        result = analyze_microstrip(3.05, sub, freq_hz=10e9)
        assert result["alpha_d_db_per_mm"] == 0.0

    def test_low_loss_substrate_much_lower_alpha_d_than_fr4(self):
        """Duroid 5880 (tan_d=0.0009) vs FR-4 (tan_d=0.02): α_d ratio
        should match tan_d ratio (~22×). This catches missing-tan_d
        bugs that would silently equalise the two."""
        fr4 = Substrate(er=4.2, h_mm=1.58, t_um=17.4, tan_d=0.02)
        duroid = Substrate(er=2.2, h_mm=1.58, t_um=17.4, tan_d=0.0009)
        f = 10e9
        # Use the same width on both — we're comparing α_d sensitivity to
        # tan_d, not to εr. (Yes, εr also scales α_d slightly; the
        # dominant factor is still the 22× tan_d ratio.)
        ad_fr4 = analyze_microstrip(3.05, fr4, freq_hz=f)["alpha_d_db_per_mm"]
        ad_duroid = analyze_microstrip(3.05, duroid, freq_hz=f)["alpha_d_db_per_mm"]
        assert ad_fr4 / ad_duroid > 10  # at least a decade lower on PTFE


class TestMicrostripLossOrderOfMagnitude:
    """Loss values must be physically plausible: not zero (would mean
    tan_d is being ignored), not absurdly high (would indicate a unit
    or factor error)."""

    @pytest.mark.parametrize(
        ("preset", "freq_hz"),
        [
            ("FR4", 1e9),
            ("FR4", 10e9),
            ("Rogers", 5e9),
            ("Duroid", 28e9),
        ],
    )
    def test_loss_in_plausible_range(self, preset, freq_hz):
        if preset == "FR4":
            sub = Substrate(er=4.2, h_mm=1.58, t_um=17.4, tan_d=0.02)
        elif preset == "Rogers":
            sub = Substrate(er=3.66, h_mm=0.508, t_um=35.0, tan_d=0.0037)
        else:  # Duroid
            sub = Substrate(er=2.2, h_mm=0.508, t_um=35.0, tan_d=0.0009)

        result = analyze_microstrip(1.0, sub, freq_hz=freq_hz)
        for key in ("alpha_d_db_per_mm", "alpha_c_db_per_mm", "alpha_total_db_per_mm"):
            v = result[key]
            assert 1e-6 < v < 0.5, (
                f"{preset} at {freq_hz / 1e9:.1f} GHz: {key} = {v} dB/mm "
                f"is outside 1e-6 .. 0.5 plausible range"
            )


class TestMicrostripWidthHeightExtremes:
    """W/h ≪ 1 (high-Z line) and W/h ≫ 1 (low-Z line) are the two
    boundary regimes where the synthesis formula switches branches.
    Bugs hide right at the seam.
    """

    def test_high_impedance_line_synthesizes(self):
        """100 Ω on a thick FR-4 substrate → W/h < 1."""
        from mcp_qucs_s.microstrip import synthesize_microstrip_line

        sub = Substrate(er=4.4, h_mm=1.524, t_um=35.0, tan_d=0.02)  # 60 mil FR-4
        line = synthesize_microstrip_line(
            z0_ohm=100.0, electrical_length_deg=90, freq_hz=2.4e9, substrate=sub
        )
        assert line.width_mm / sub.h_mm < 1.0, (
            f"100 Ω on 1.524 mm FR-4 should give a narrow line (W/h < 1); "
            f"got W/h = {line.width_mm / sub.h_mm:.3f}"
        )
        # Round-trip analyse: synthesised line should give Z₀ within 5% of target
        result = analyze_microstrip(line.width_mm, sub, freq_hz=2.4e9)
        assert abs(result["z0_ohm"] - 100.0) / 100.0 < 0.05

    def test_low_impedance_line_synthesizes(self):
        """20 Ω on a thin Rogers substrate → W/h ≫ 1."""
        from mcp_qucs_s.microstrip import synthesize_microstrip_line

        sub = Substrate(er=3.66, h_mm=0.254, t_um=35.0, tan_d=0.0037)  # 10 mil Rogers
        line = synthesize_microstrip_line(
            z0_ohm=20.0, electrical_length_deg=90, freq_hz=5e9, substrate=sub
        )
        assert line.width_mm / sub.h_mm > 3, (
            f"20 Ω on 0.254 mm Rogers should give a wide line (W/h > 3); "
            f"got W/h = {line.width_mm / sub.h_mm:.3f}"
        )
        result = analyze_microstrip(line.width_mm, sub, freq_hz=5e9)
        assert abs(result["z0_ohm"] - 20.0) / 20.0 < 0.05
