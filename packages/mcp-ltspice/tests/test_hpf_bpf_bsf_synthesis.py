"""Tests for HPF / BPF / BSF synthesis via classical LPF→X frequency transformations.

Reference: Pozar, *Microwave Engineering* 4th ed., §8.5.

Key invariants:
- HPF: series Ls become series Cs; shunt Cs become shunt Ls. Component
  count equals the LPF prototype's. f_c at the -3 dB cutoff (Butterworth).
- BPF: each LPF reactive element becomes two BPF elements. Total
  reactive count = 2 × N. f_0 = √(f_low · f_high). Each trap pair
  resonates at f_0.
- BSF: same component-count as BPF; series elements anti-resonate at
  f_0, shunt elements resonate at f_0.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from mcp_ltspice.extract import (
    components_dict_to_elements,
    ladder_sparams_from_components,
)
from mcp_ltspice.synthesis import (
    synthesize_lc_bpf,
    synthesize_lc_bsf,
    synthesize_lc_hpf,
    synthesize_lc_lpf,
)

# ---------------------------------------------------------------------------
# HPF synthesis
# ---------------------------------------------------------------------------


class TestHpfSynthesis:
    def test_basic_butterworth_5th_order(self):
        d = synthesize_lc_hpf("butterworth", order=5, cutoff_hz=1e9)
        # 5 reactive elements: 3 series-C + 2 shunt-L
        c_count = sum(1 for k in d.components if k.startswith("C"))
        l_count = sum(1 for k in d.components if k.startswith("L"))
        assert c_count == 3
        assert l_count == 2
        assert d.metadata["kind"] == "highpass"

    def test_chebyshev_3rd_order(self):
        d = synthesize_lc_hpf("chebyshev1", order=3, cutoff_hz=2.4e9, ripple_db=0.5)
        assert len(d.components) == 3
        assert d.cutoff_hz == 2.4e9

    def test_elliptic_is_implemented(self):
        """Elliptic HPF now synthesises; see test_elliptic_hpf.py for the
        full response validation against theory and qucsator."""
        d = synthesize_lc_hpf("elliptic", order=5, cutoff_hz=1e9, stopband_atten_db=40)
        assert d.filter_type == "elliptic"
        assert d.transmission_zeros_hz, "elliptic HPF must have finite zeros"
        assert all(z < 1e9 for z in d.transmission_zeros_hz), "HPF zeros belong below fc"

    def test_response_is_actually_highpass(self):
        """Sanity check: HPF should attenuate DC, pass high freq."""
        d = synthesize_lc_hpf("butterworth", order=5, cutoff_hz=1e9)
        elements = components_dict_to_elements(
            d.components, topology="series_first", kind="highpass"
        )
        f = np.geomspace(1e6, 10e9, 1001)
        s = ladder_sparams_from_components(elements, f, z0=50.0)
        s21_db = 20.0 * np.log10(np.maximum(np.abs(s[:, 1, 0]), 1e-12))

        # Attenuation deep below fc (1 MHz << 1 GHz)
        idx_low = int(np.argmin(np.abs(f - 1e7)))  # 10 MHz, 2 decades below fc
        assert s21_db[idx_low] < -40, (
            f"HPF should attenuate at 10 MHz; got {s21_db[idx_low]:.1f} dB"
        )

        # Passband at high freq (5 GHz, well above fc)
        idx_high = int(np.argmin(np.abs(f - 5e9)))
        assert s21_db[idx_high] > -1.0, (
            f"HPF passband should be lossless above fc; got {s21_db[idx_high]:.1f} dB at 5 GHz"
        )

        # -3 dB at fc (with some tolerance for finite freq grid)
        idx_fc = int(np.argmin(np.abs(f - 1e9)))
        assert -4.5 < s21_db[idx_fc] < -1.5, (
            f"HPF should be near -3 dB at fc=1 GHz; got {s21_db[idx_fc]:.2f} dB"
        )


# ---------------------------------------------------------------------------
# BPF synthesis
# ---------------------------------------------------------------------------


class TestBpfSynthesis:
    def test_basic_3rd_order_butterworth(self):
        d = synthesize_lc_bpf("butterworth", order=3, f_low_hz=900e6, f_high_hz=1100e6)
        # 3rd-order LPF prototype → 6 BPF reactive elements
        assert len(d.components) == 6
        # Geometric mean
        assert d.metadata["f_0_hz"] == pytest.approx(math.sqrt(900e6 * 1100e6), rel=1e-9)
        # Fractional bandwidth ≈ (1100 - 900) / 994.99 ≈ 0.201
        assert d.metadata["fractional_bandwidth"] == pytest.approx(
            (2 * math.pi * 1100e6 - 2 * math.pi * 900e6)
            / math.sqrt(2 * math.pi * 1100e6 * 2 * math.pi * 900e6),
            rel=1e-9,
        )

    def test_each_trap_pair_resonates_at_f0(self):
        """Series-LC and shunt-LC pairs should each resonate at f₀."""
        d = synthesize_lc_bpf("butterworth", order=3, f_low_hz=900e6, f_high_hz=1100e6)
        f0 = d.metadata["f_0_hz"]
        # Find each L+C pair (with or without _s suffix on C)
        for k in [1, 2, 3]:
            l_key = f"L{k}"
            # Series-LC: L_k + C_k_s
            c_s_key = f"C{k}_s"
            c_key = f"C{k}"
            if l_key in d.components and c_s_key in d.components:
                f_res = 1.0 / (2 * math.pi * math.sqrt(d.components[l_key] * d.components[c_s_key]))
                assert f_res == pytest.approx(f0, rel=1e-3), (
                    f"Series-LC pair {l_key}+{c_s_key} should resonate at f_0={f0 / 1e6:.1f} MHz; "
                    f"got {f_res / 1e6:.1f} MHz"
                )
            elif l_key in d.components and c_key in d.components:
                f_res = 1.0 / (2 * math.pi * math.sqrt(d.components[l_key] * d.components[c_key]))
                assert f_res == pytest.approx(f0, rel=1e-3), (
                    f"Shunt-LC pair {l_key}+{c_key} should resonate at f_0={f0 / 1e6:.1f} MHz; "
                    f"got {f_res / 1e6:.1f} MHz"
                )

    def test_invalid_band_raises(self):
        with pytest.raises(ValueError, match="must exceed"):
            synthesize_lc_bpf("butterworth", order=3, f_low_hz=1100e6, f_high_hz=900e6)

    def test_elliptic_raises(self):
        with pytest.raises(NotImplementedError):
            synthesize_lc_bpf("elliptic", order=5, f_low_hz=900e6, f_high_hz=1100e6)


class TestBpfResponse:
    """Verify BPF response shape via the analytical ABCD chain."""

    def _bpf_response(self, order: int, f_low: float, f_high: float):
        d = synthesize_lc_bpf("butterworth", order=order, f_low_hz=f_low, f_high_hz=f_high)
        elements = components_dict_to_elements(
            d.components, kind="bandpass", topology="series_first"
        )
        f = np.geomspace(0.01 * d.metadata["f_0_hz"], 100 * d.metadata["f_0_hz"], 2001)
        s = ladder_sparams_from_components(elements, f, z0=50.0)
        s21_db = 20.0 * np.log10(np.maximum(np.abs(s[:, 1, 0]), 1e-12))
        return f, s21_db, d.metadata["f_0_hz"]

    def test_passband_lossless_at_f0(self):
        f, s21, f0 = self._bpf_response(3, 900e6, 1100e6)
        idx = int(np.argmin(np.abs(f - f0)))
        assert s21[idx] > -0.5, f"BPF at f_0 should be near 0 dB; got {s21[idx]:.2f} dB"

    def test_neg_3db_at_band_edges(self):
        f, s21, _ = self._bpf_response(3, 900e6, 1100e6)
        for f_edge in (900e6, 1100e6):
            idx = int(np.argmin(np.abs(f - f_edge)))
            assert -4.5 < s21[idx] < -1.5, (
                f"BPF at edge {f_edge / 1e6:.0f} MHz should be near -3 dB; got {s21[idx]:.2f} dB"
            )

    def test_stopband_attenuation(self):
        """At one decade outside the band, BPF should attenuate >> 30 dB."""
        f, s21, f0 = self._bpf_response(3, 900e6, 1100e6)
        idx_low = int(np.argmin(np.abs(f - f0 / 10)))
        idx_high = int(np.argmin(np.abs(f - f0 * 10)))
        assert s21[idx_low] < -40, f"BPF should reject 1 decade below; got {s21[idx_low]:.1f} dB"
        assert s21[idx_high] < -40, f"BPF should reject 1 decade above; got {s21[idx_high]:.1f} dB"


# ---------------------------------------------------------------------------
# BSF synthesis
# ---------------------------------------------------------------------------


class TestBsfSynthesis:
    def test_basic_3rd_order_butterworth(self):
        d = synthesize_lc_bsf("butterworth", order=3, f_low_hz=900e6, f_high_hz=1100e6)
        assert len(d.components) == 6
        assert d.metadata["kind"] == "bandstop"

    def test_each_trap_pair_resonates_at_f0(self):
        """Same f₀ check as BPF — both BPF and BSF have LC pairs at f₀."""
        d = synthesize_lc_bsf("butterworth", order=3, f_low_hz=900e6, f_high_hz=1100e6)
        f0 = d.metadata["f_0_hz"]
        for k in [1, 2, 3]:
            l_key, c_s_key, c_key = f"L{k}", f"C{k}_s", f"C{k}"
            if l_key in d.components and c_s_key in d.components:
                f_res = 1.0 / (2 * math.pi * math.sqrt(d.components[l_key] * d.components[c_s_key]))
                assert f_res == pytest.approx(f0, rel=1e-3)
            elif l_key in d.components and c_key in d.components:
                f_res = 1.0 / (2 * math.pi * math.sqrt(d.components[l_key] * d.components[c_key]))
                assert f_res == pytest.approx(f0, rel=1e-3)


class TestBsfResponse:
    """Verify BSF response shape via the analytical ABCD chain."""

    def _bsf_response(self, order: int, f_low: float, f_high: float):
        d = synthesize_lc_bsf("butterworth", order=order, f_low_hz=f_low, f_high_hz=f_high)
        elements = components_dict_to_elements(
            d.components, kind="bandstop", topology="series_first"
        )
        f = np.geomspace(0.01 * d.metadata["f_0_hz"], 100 * d.metadata["f_0_hz"], 2001)
        s = ladder_sparams_from_components(elements, f, z0=50.0)
        s21_db = 20.0 * np.log10(np.maximum(np.abs(s[:, 1, 0]), 1e-12))
        return f, s21_db, d.metadata["f_0_hz"]

    def test_deep_notch_at_f0(self):
        """At f_0 the BSF should show a deep notch (>> 60 dB rejection)."""
        f, s21, f0 = self._bsf_response(3, 900e6, 1100e6)
        idx = int(np.argmin(np.abs(f - f0)))
        assert s21[idx] < -60, f"BSF at f_0 should notch deeply; got {s21[idx]:.1f} dB"

    def test_passband_outside_stopband(self):
        """At one decade outside the stopband, BSF should pass cleanly."""
        f, s21, f0 = self._bsf_response(3, 900e6, 1100e6)
        idx_low = int(np.argmin(np.abs(f - f0 / 10)))
        idx_high = int(np.argmin(np.abs(f - f0 * 10)))
        assert s21[idx_low] > -1.0, (
            f"BSF passband should be lossless 1 decade below; got {s21[idx_low]:.2f} dB"
        )
        assert s21[idx_high] > -1.0, (
            f"BSF passband should be lossless 1 decade above; got {s21[idx_high]:.2f} dB"
        )

    def test_neg_3db_at_band_edges(self):
        """The stopband is bounded by -3 dB at f_low / f_high."""
        f, s21, _ = self._bsf_response(3, 900e6, 1100e6)
        for f_edge in (900e6, 1100e6):
            idx = int(np.argmin(np.abs(f - f_edge)))
            assert -4.5 < s21[idx] < -1.5, (
                f"BSF at edge {f_edge / 1e6:.0f} MHz should be near -3 dB; got {s21[idx]:.2f} dB"
            )


# ---------------------------------------------------------------------------
# Cross-cutting
# ---------------------------------------------------------------------------


class TestImpedanceScaling:
    """Doubling Z₀ should double L values and halve C values across all kinds."""

    @pytest.mark.parametrize(
        ("synth", "kwargs"),
        [
            (
                synthesize_lc_lpf,
                {"filter_type": "butterworth", "order": 5, "cutoff_hz": 1e9},
            ),
            (
                synthesize_lc_hpf,
                {"filter_type": "butterworth", "order": 5, "cutoff_hz": 1e9},
            ),
            (
                synthesize_lc_bpf,
                {"filter_type": "butterworth", "order": 3, "f_low_hz": 900e6, "f_high_hz": 1100e6},
            ),
            (
                synthesize_lc_bsf,
                {"filter_type": "butterworth", "order": 3, "f_low_hz": 900e6, "f_high_hz": 1100e6},
            ),
        ],
    )
    def test_z0_scaling(self, synth, kwargs):
        d_50 = synth(**kwargs, z0=50.0)
        d_100 = synth(**kwargs, z0=100.0)
        for ref in d_50.components:
            v_50 = d_50.components[ref]
            v_100 = d_100.components[ref]
            if ref.startswith("L"):
                # Doubling Z0 doubles L
                assert v_100 == pytest.approx(2.0 * v_50, rel=1e-9), (
                    f"{ref}: Z0 100→50 should double L; got {v_50:.3e} vs {v_100:.3e}"
                )
            else:
                # Doubling Z0 halves C
                assert v_100 == pytest.approx(0.5 * v_50, rel=1e-9), (
                    f"{ref}: Z0 100→50 should halve C; got {v_50:.3e} vs {v_100:.3e}"
                )
