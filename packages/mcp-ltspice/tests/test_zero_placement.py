"""Tests for transmission-zero placement in shunt LC traps."""

from __future__ import annotations

import math

import numpy as np
import pytest
from mcp_ltspice.extract import (
    components_dict_to_elements,
    ladder_sparams_from_components,
)
from mcp_ltspice.synthesis import (
    place_transmission_zero,
    synthesize_lc_lpf,
    trap_lc_for_freq,
)


def _resonance_hz(l_h: float, c_f: float) -> float:
    return 1.0 / (2 * math.pi * math.sqrt(l_h * c_f))


def test_trap_lc_for_freq_preserves_ratio() -> None:
    l_old, c_old = 4.7e-9, 1.8e-12
    f_target = 2.0e9
    l_new, c_new = trap_lc_for_freq(
        f_target, l_existing=l_old, c_existing=c_old, preserve_ratio=True
    )
    assert _resonance_hz(l_new, c_new) == pytest.approx(f_target, rel=1e-9)
    # Ratio L/C preserved
    assert (l_new / c_new) == pytest.approx(l_old / c_old, rel=1e-9)


def test_trap_lc_for_freq_holds_one_fixed() -> None:
    l_fixed = 4.7e-9
    f_target = 1.85e9
    l_new, c_new = trap_lc_for_freq(f_target, l_existing=l_fixed)
    assert l_new == pytest.approx(l_fixed)
    assert _resonance_hz(l_new, c_new) == pytest.approx(f_target, rel=1e-9)


def test_place_transmission_zero_moves_notch(tmp_path) -> None:
    # Synthesize a 5th-order elliptic LPF and check that moving its first
    # trap relocates the notch in S21.
    design = synthesize_lc_lpf(
        "elliptic", order=5, cutoff_hz=1e9, ripple_db=0.1, stopband_atten_db=30
    )
    f_target = 1.5e9
    res = place_transmission_zero(
        design.components, trap_index=2, target_freq_hz=f_target,
        preserve_ratio=True, snap_series=None,  # don't snap so we can test math precisely
    )
    new_comps = res["components"]

    # Achieved resonance equals target (no snap)
    achieved = _resonance_hz(new_comps["L2"], new_comps["C2"])
    assert achieved == pytest.approx(f_target, rel=1e-9)
    assert res["achieved_freq_hz"] == pytest.approx(f_target, rel=1e-9)

    # The S21 response should now have a deep notch near 1.5 GHz
    f = np.linspace(1.4e9, 1.6e9, 201)
    s = ladder_sparams_from_components(
        components_dict_to_elements(
            new_comps, topology="series_first", transmission_zeros=True
        ),
        f,
    )
    s21_db = 20 * np.log10(np.abs(s[:, 1, 0]) + 1e-12)
    notch_idx = int(np.argmin(s21_db))
    notch_freq = f[notch_idx]
    assert abs(notch_freq - f_target) / f_target < 0.02
    assert s21_db[notch_idx] < -40  # should be a deep notch (lossless)


def test_place_transmission_zero_with_e24_snap() -> None:
    design = synthesize_lc_lpf(
        "elliptic", order=5, cutoff_hz=1e9, ripple_db=0.1, stopband_atten_db=30
    )
    res = place_transmission_zero(
        design.components, trap_index=2, target_freq_hz=1.85e9,
        preserve_ratio=True, snap_series="E24",
    )
    # E24 snap usually pulls achieved within ~5% of target
    assert abs(res["freq_error_pct"]) < 10.0


def test_place_transmission_zero_invalid_index_raises() -> None:
    design = synthesize_lc_lpf(
        "elliptic", order=5, cutoff_hz=1e9, ripple_db=0.1, stopband_atten_db=30
    )
    with pytest.raises(KeyError):
        place_transmission_zero(design.components, trap_index=99, target_freq_hz=2e9)
