"""Closed-form anchors for the EMC emission predictors (issue #36).

The predictors output regulatory judgements, so a factor-of-2π or a
µV-vs-V slip flips pass to fail with no exception anywhere. These tests
pin both predictors to hand calculations written out in full.
"""

from __future__ import annotations

import math

import pytest

from mcp_rf_analysis.emc.conducted import LISNModel, lisn_impedance, predict_conducted_emissions
from mcp_rf_analysis.emc.radiated import predict_radiated_emissions_loop


def test_radiated_golden_value_against_otts_constant() -> None:
    """Textbook far-field small loop (Ott): E = 131.6e-16 · f² · A · I / r.

    f = 100 MHz, A = 1 cm² = 1e-4 m², I = 1 mA, r = 3 m:
        E = 131.6e-16 · (1e8)² · 1e-4 · 1e-3 / 3
          = 131.6e-16 · 1e16 · 1e-7 / 3 = 4.3867e-6 V/m = 4.3867 µV/m
          → 20·log10(4.3867) = 12.84 dBµV/m.
    The module uses the exact constant η₀·π/c² = 131.68e-16 (0.005 dB
    from Ott's rounded 131.6), so 0.05 dB tolerance is comfortable.
    """
    r = predict_radiated_emissions_loop(
        current_a=1e-3, loop_area_cm2=1.0, freq_hz=100e6, measurement_distance_m=3.0
    )
    e_expected = 131.6e-16 * (1e8) ** 2 * 1e-4 * 1e-3 / 3.0
    assert r["e_v_per_m"] == pytest.approx(e_expected, rel=1e-3)
    assert r["e_dbuv_per_m"] == pytest.approx(20 * math.log10(e_expected * 1e6), abs=0.05)
    assert r["e_dbuv_per_m"] == pytest.approx(12.84, abs=0.05)


def test_radiated_dimensional_consistency() -> None:
    """Doubling loop area: +6.02 dB. Doubling distance: −6.02 dB."""
    base = predict_radiated_emissions_loop(
        current_a=1e-3, loop_area_cm2=2.0, freq_hz=200e6, measurement_distance_m=3.0
    )
    doubled = predict_radiated_emissions_loop(
        current_a=1e-3, loop_area_cm2=4.0, freq_hz=200e6, measurement_distance_m=3.0
    )
    far = predict_radiated_emissions_loop(
        current_a=1e-3, loop_area_cm2=2.0, freq_hz=200e6, measurement_distance_m=6.0
    )
    assert doubled["e_dbuv_per_m"] - base["e_dbuv_per_m"] == pytest.approx(6.0206, abs=0.01)
    assert far["e_dbuv_per_m"] - base["e_dbuv_per_m"] == pytest.approx(-6.0206, abs=0.01)


def test_radiated_near_field_guard() -> None:
    """At 10 MHz the radian sphere is λ/2π = 4.77 m — a 3 m measurement
    is inside it, where the far-field formula under-predicts. The
    predictor must refuse, not return a wrong regulatory number."""
    with pytest.raises(ValueError, match=r"[Nn]ear-field"):
        predict_radiated_emissions_loop(
            current_a=1e-3, loop_area_cm2=10.0, freq_hz=10e6, measurement_distance_m=3.0
        )
    # Just outside the sphere is allowed
    predict_radiated_emissions_loop(
        current_a=1e-3, loop_area_cm2=10.0, freq_hz=10e6, measurement_distance_m=5.0
    )


def test_conducted_golden_value_hand_computed_lisn_divider() -> None:
    """CISPR 16 LISN at 1 MHz with I = 100 µA rms, worked by hand:

    Z_L  = j·2π·1e6·50µH        = j314.159 Ω;  branch1 = 5 + j314.159
    Z_C  = 1/(j·2π·1e6·0.1µF)   = −j1.5915 Ω;  branch2 = 50 − j1.5915
    num  = branch1·branch2      = 750.0 + j15700.4
    den  = branch1 + branch2    = 55 + j312.57
    |Z|  = |num|/|den|          = 15718.3 / 317.37 = 49.53 Ω

    V = 49.53 Ω · 100 µA = 4.953 mV → 20·log10(4953 µV) = 73.90 dBµV
    """
    z = lisn_impedance(1e6, LISNModel())
    assert abs(z) == pytest.approx(49.53, abs=0.02)

    res = predict_conducted_emissions([(1e6, 100e-6)], standard="cispr22_b", margin_db=6.0)
    assert res["freq_hz"] == [1e6]
    assert res["measured_dbuv"][0] == pytest.approx(73.90, abs=0.05)
    # limit at 1 MHz (CISPR 22 B) is 56 dBµV; with the 6 dB buffer the
    # margin is 56 − 6 − 73.90 = −23.90 → fail.
    assert res["limit_dbuv"][0] == pytest.approx(56.0, abs=0.01)
    assert res["margin_db_per_freq"][0] == pytest.approx(-23.90, abs=0.06)
    assert res["overall"] == "fail"


def test_conducted_current_scaling_is_20db_per_decade() -> None:
    a = predict_conducted_emissions([(1e6, 10e-6)])
    b = predict_conducted_emissions([(1e6, 100e-6)])
    assert b["measured_dbuv"][0] - a["measured_dbuv"][0] == pytest.approx(20.0, abs=1e-6)
