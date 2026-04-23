"""Tests for the analog active-filter synthesis tools."""

from __future__ import annotations

import math

import numpy as np
import pytest

from mcp_ltspice.analog import (
    cascaded_lpf_design,
    mfb_band_pass,
    mfb_low_pass,
    sallen_key_band_pass,
    sallen_key_high_pass,
    sallen_key_low_pass,
)
from mcp_ltspice.analog.cascade import transfer_function_db

# ---- Sallen-Key LPF ------------------------------------------------------


def test_sallen_key_lpf_unity_gain_butterworth() -> None:
    """Butterworth Q = 1/√2; equal-C unity gain → R1 = √2/(ω·C), R2 = 1/(√2·ω·C)."""
    fc = 10e3
    q = 1 / math.sqrt(2)
    d = sallen_key_low_pass(fc, q=q, gain_v_v=1.0, c_pf=10000.0)
    assert d.topology == "lpf"
    assert d.gain_v_v == 1.0
    omega = 2 * math.pi * fc
    expected_r1 = 1.0 / (q * omega * d.C1)
    expected_r2 = q / (omega * d.C1)
    assert pytest.approx(expected_r1, rel=1e-9) == d.R1
    assert pytest.approx(expected_r2, rel=1e-9) == d.R2


def test_sallen_key_lpf_warns_at_high_q() -> None:
    d = sallen_key_low_pass(1e3, q=20.0)
    assert any("Sallen-Key" in n or "MFB" in n for n in d.notes)


def test_sallen_key_lpf_invalid_inputs_raise() -> None:
    with pytest.raises(ValueError):
        sallen_key_low_pass(0)
    with pytest.raises(ValueError):
        sallen_key_low_pass(1e3, q=-1)


def test_sallen_key_hpf_returns_capacitors() -> None:
    d = sallen_key_high_pass(20e3, q=1.0)
    assert d.topology == "hpf"
    assert d.C1 > 0 and d.C2 > 0


def test_sallen_key_bpf_gain_is_q_dependent() -> None:
    """For Sallen-Key BPF, K = 3 - √2/Q. Q=1 → K = 3 - √2 ≈ 1.586."""
    d = sallen_key_band_pass(1e3, q=1.0)
    assert d.topology == "bpf"
    expected_k = 3 - math.sqrt(2)
    assert d.gain_v_v == pytest.approx(expected_k, rel=1e-6)


# ---- MFB -----------------------------------------------------------------


def test_mfb_lpf_unity_gain() -> None:
    fc = 1e3
    d = mfb_low_pass(fc, q=1 / math.sqrt(2), gain_v_v=1.0)
    assert d.topology == "lpf"
    assert d.R1 > 0 and d.R2 > 0 and d.R3 > 0
    assert d.C1 > 0 and d.C2 > 0
    # Inversion warning is always present
    assert any("inverted" in n.lower() for n in d.notes)


def test_mfb_lpf_higher_gain_uses_smaller_r1() -> None:
    """K = -R2/R1 → higher |K| means smaller R1 for fixed R2."""
    d_unity = mfb_low_pass(1e3, q=0.7, gain_v_v=1.0)
    d_high = mfb_low_pass(1e3, q=0.7, gain_v_v=10.0)
    assert d_high.R1 < d_unity.R1


def test_mfb_bpf_q_high_passes() -> None:
    """MFB BPF should handle Q up to ~25 cleanly."""
    d = mfb_band_pass(10e3, q=10.0, gain_v_v=1.0, c_pf=10.0)
    assert d.R1 > 0 and d.R2 > 0 and d.R3 > 0


# ---- cascaded LPF --------------------------------------------------------


@pytest.mark.parametrize("order", [2, 3, 4, 5, 6, 7, 8])
def test_cascaded_butterworth_each_order_works(order: int) -> None:
    d = cascaded_lpf_design(fc_hz=10e3, order=order, response="butterworth")
    assert d["order"] == order
    # Number of 2nd-order op-amp stages = order // 2
    assert d["n_op_amps_required"] == order // 2
    # Each 2nd-order stage has Q in (0, 3)
    for stage in d["stages"]:
        if stage["q"] is not None:
            assert 0.4 < stage["q"] < 3.0


def test_cascaded_unknown_order_raises() -> None:
    with pytest.raises(ValueError):
        cascaded_lpf_design(fc_hz=1e3, order=99)


def test_cascaded_bessel_higher_fc_norm() -> None:
    """Bessel filters have fc_norm > 1 because the table normalizes to a
    max-flat group-delay metric, not the -3 dB point."""
    d = cascaded_lpf_design(fc_hz=10e3, order=4, response="bessel")
    for stage in d["stages"]:
        if stage["q"] is not None:
            assert stage["fc_hz"] > 10e3


# ---- transfer function ---------------------------------------------------


def test_butterworth_transfer_function_minus_3db_at_fc() -> None:
    """All Butterworth orders are -3 dB at fc by construction."""
    fc = 10e3
    for order in [2, 3, 4, 5, 6]:
        tf = transfer_function_db(fc, order, response="butterworth")
        f = np.array(tf["freq_hz"])
        h = np.array(tf["h_db"])
        idx = int(np.argmin(np.abs(f - fc)))
        assert h[idx] == pytest.approx(-3.0, abs=0.5), (
            f"Order {order}: |H(fc)| = {h[idx]:.2f} dB, expected ~-3"
        )


def test_butterworth_higher_order_steeper_rolloff() -> None:
    """Butterworth roll-off rate is 20·N dB / decade above fc."""
    fc = 10e3
    tf3 = transfer_function_db(fc, 3, response="butterworth")
    tf6 = transfer_function_db(fc, 6, response="butterworth")
    # At 10·fc:
    f3 = np.array(tf3["freq_hz"])
    h3 = np.array(tf3["h_db"])
    f6 = np.array(tf6["freq_hz"])
    h6 = np.array(tf6["h_db"])
    h3_at_10fc = float(np.interp(10 * fc, f3, h3))
    h6_at_10fc = float(np.interp(10 * fc, f6, h6))
    # 6th-order should have 60 dB more attenuation than 3rd at 10·fc
    assert h6_at_10fc < h3_at_10fc - 30
