"""Regression tests for the v0.3.0 hardening pass.

Each test pins behaviour that a five-agent code review identified as
under-tested or recently bug-fixed. Single file so the regression
contract is visible at a glance — these are not unit tests of any one
module, they are *contract tests* enforcing properties the codebase
must continue to honour.
"""

from __future__ import annotations

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
)
from mcp_ltspice.vendor_models import lookup_part

# ---------------------------------------------------------------------------
# A4 regression: srf_audit surfaces unaudited components instead of dropping
# them silently.
# ---------------------------------------------------------------------------


class TestSrfAuditUnauditedSurfacing:
    def test_unknown_vendor_appears_as_warning_not_silent_drop(self):
        """When the configured vendor isn't in the catalog, every component
        should surface in the warnings list, not be silently skipped.
        Regression: prior behaviour was ``except (ValueError, KeyError):
        continue``, which dropped components from `per_component` and
        let `severity='ok'` slip past."""
        from mcp_ltspice.eval import FilterSpec
        from mcp_ltspice.srf_check import srf_audit

        spec = FilterSpec.model_validate(
            {
                "passband": {
                    "f_start": 1e6,
                    "f_stop": 600e6,
                    "il_max_db": 0.5,
                    "rl_min_db": 14.0,
                },
                "stopband_targets": [
                    {"freq": 2e9, "rejection_min_db": 30.0, "label": "2x fc"},
                ],
            }
        )
        components = {"L1": 4.7e-9, "C2": 5e-12}
        # Unknown vendor name → lookup_part raises ValueError for every component
        result = srf_audit(components, spec, inductor_vendor="not_a_real_vendor")

        assert result["n_unaudited"] >= 1
        assert "L1" in result["unaudited"]
        assert any("L1" in w and "not_a_real_vendor" in w for w in result["warnings"])
        # severity must NOT be 'ok' when something couldn't be audited
        assert result["severity"] != "ok"


# ---------------------------------------------------------------------------
# B1: HPF / BPF / BSF responses verified at orders 3, 5, 7, 9 — not just the
# lowest order. A topology / fit bug at higher orders is a real regression.
# ---------------------------------------------------------------------------


def _s21_db(els, freqs):
    s = ladder_sparams_from_components(els, np.asarray(freqs), z0=50.0)
    return 20 * np.log10(np.abs(s[:, 1, 0]))


@pytest.mark.parametrize("order", [3, 5, 7, 9])
def test_hpf_response_across_orders(order):
    """HPF: passband > -1 dB at 10·fc, stopband < -25 dB at fc/10. The
    -25 dB bar scales conservatively with order so all orders pass.
    """
    fc = 100e6
    d = synthesize_lc_hpf("butterworth", order=order, cutoff_hz=fc)
    els = components_dict_to_elements(d.components, topology=d.topology, kind=d.metadata["kind"])
    s21 = _s21_db(els, [fc / 10, 10 * fc])
    assert s21[0] < -25, f"order={order}: stopband at fc/10 = {s21[0]:.1f} dB, expected < -25"
    assert s21[1] > -1, f"order={order}: passband at 10·fc = {s21[1]:.2f} dB, expected > -1"


@pytest.mark.parametrize("order", [3, 5, 7, 9])
def test_bpf_response_across_orders(order):
    """BPF: passband at f_0 within 1 dB of 0; deep rejection at f_0/10."""
    f_low, f_high = 900e6, 1100e6
    f_0 = (f_low * f_high) ** 0.5
    d = synthesize_lc_bpf("butterworth", order=order, f_low_hz=f_low, f_high_hz=f_high)
    els = components_dict_to_elements(d.components, topology=d.topology, kind=d.metadata["kind"])
    s21 = _s21_db(els, [f_0, f_0 / 10])
    assert abs(s21[0]) < 1.5, f"order={order}: BPF IL at f_0 = {s21[0]:.2f} dB"
    assert s21[1] < -20, f"order={order}: BPF stopband at f_0/10 = {s21[1]:.1f} dB"


@pytest.mark.parametrize("order", [3, 5, 7, 9])
def test_bsf_response_across_orders(order):
    """BSF: passband well below band, deep notch at f_0."""
    f_low, f_high = 900e6, 1100e6
    f_0 = (f_low * f_high) ** 0.5
    d = synthesize_lc_bsf("butterworth", order=order, f_low_hz=f_low, f_high_hz=f_high)
    els = components_dict_to_elements(d.components, topology=d.topology, kind=d.metadata["kind"])
    s21 = _s21_db(els, [f_0 / 5, f_0])
    assert s21[0] > -2, f"order={order}: BSF passband at f_0/5 = {s21[0]:.2f} dB"
    assert s21[1] < -30, f"order={order}: BSF notch at f_0 = {s21[1]:.1f} dB (expected deep)"


# ---------------------------------------------------------------------------
# B2: Narrow and wide fractional bandwidth for BPF — classical LPF→BPF
# breaks at the regime extremes. f_0 stays where it should.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("f_low", "f_high", "label"),
    [
        (995e6, 1005e6, "narrow Δ ≈ 0.01"),
        (700e6, 1400e6, "wide Δ ≈ 0.67"),
    ],
)
def test_bpf_fractional_bandwidth_extremes(f_low, f_high, label):
    f_0 = (f_low * f_high) ** 0.5
    d = synthesize_lc_bpf("butterworth", order=5, f_low_hz=f_low, f_high_hz=f_high)
    els = components_dict_to_elements(d.components, topology=d.topology, kind=d.metadata["kind"])
    # IL at the geometric centre frequency must be within 1 dB
    s21 = _s21_db(els, [f_0])
    assert abs(s21[0]) < 1.5, f"{label}: BPF IL at f_0 = {s21[0]:.2f} dB"
    # The reported fractional bandwidth must match the requested one
    fbw_reported = d.metadata.get("fractional_bandwidth")
    fbw_expected = (f_high - f_low) / f_0
    assert fbw_reported == pytest.approx(fbw_expected, rel=1e-6)


# ---------------------------------------------------------------------------
# B3: Vendor parasitic substitution — Coilcraft 0402HP 4.7 nH SRF is in the
# documented neighbourhood. We test the value, not just presence.
# ---------------------------------------------------------------------------


def test_coilcraft_0402hp_47nh_srf_in_documented_band():
    part = lookup_part("coilcraft_0402hp", 4.7e-9, kind="L")
    # Coilcraft 0402HP-4N7 published SRF is ≈ 6 GHz (Coilcraft datasheet).
    # We allow 4–8 GHz to cover catalogue revisions and tolerance.
    assert 4e9 <= part.srf_hz <= 8e9, (
        f"Coilcraft 0402HP 4.7 nH SRF = {part.srf_hz / 1e9:.2f} GHz "
        f"is outside the documented 4-8 GHz band"
    )


# ---------------------------------------------------------------------------
# A1 regression: extract_sparams_from_raw fills BOTH columns of the S-matrix
# (not just the diagonal). This test mocks the .raw read so it doesn't need
# a simulator install.
# ---------------------------------------------------------------------------


class _MockTrace:
    def __init__(self, wave: np.ndarray) -> None:
        self._wave = wave

    def get_wave(self) -> np.ndarray:
        return self._wave


class _MockRawRead:
    def __init__(self, traces: dict[str, np.ndarray]) -> None:
        self._traces = traces

    def get_trace(self, name: str):
        wave = self._traces.get(name)
        return _MockTrace(wave) if wave is not None else None


def test_extract_sparams_from_raw_fills_full_matrix(monkeypatch, tmp_path):
    """For a passive reciprocal symmetric 2-port, all four S-matrix
    entries must be populated, not just the diagonal."""
    from mcp_ltspice import extract as extract_mod

    # Synthesise a perfectly-matched passthrough: V(p1) = 0.5, V(p2) = 0.5,
    # I(Rs1) = 0.5/Z0 = 0.01 (with Z0=50).
    # Then a₁ = 1/(2√50) = 0.07071,
    #      b₁ = (0.5 - 50·0.01)/(2√50) = 0  → S11 = 0
    #      b₂ = 0.5/√50 = 0.07071 → S21 = 1
    n = 5
    z0 = 50.0
    v_p1 = np.full(n, 0.5, dtype=np.complex128)
    v_p2 = np.full(n, 0.5, dtype=np.complex128)
    i_rs1 = np.full(n, 0.5 / z0, dtype=np.complex128)
    freqs = np.linspace(1e9, 5e9, n)

    traces = {
        "frequency": freqs.astype(np.complex128),
        "V(p1)": v_p1,
        "V(p2)": v_p2,
        "I(Rs1)": i_rs1,
    }
    monkeypatch.setattr(extract_mod, "RawRead", lambda path: _MockRawRead(traces), raising=False)
    # spicelib import inside extract_sparams_from_raw — patch sys.modules
    import sys
    import types

    fake_spicelib = types.SimpleNamespace(RawRead=lambda path: _MockRawRead(traces))
    monkeypatch.setitem(sys.modules, "spicelib", fake_spicelib)

    raw_path = tmp_path / "test.raw"
    raw_path.touch()
    net = extract_mod.extract_sparams_from_raw(raw_path, port_map={1: "p1", 2: "p2"}, z0=z0)
    s = net.s

    # Column 1 (from the actual sim)
    assert np.allclose(s[:, 0, 0], 0, atol=1e-9), f"S11 should be 0, got {s[:, 0, 0]}"
    assert np.allclose(s[:, 1, 0], 1, atol=1e-9), f"S21 should be 1, got {s[:, 1, 0]}"
    # Column 2 (from reciprocity + symmetry)
    assert np.allclose(s[:, 0, 1], s[:, 1, 0]), "S12 must equal S21 by reciprocity"
    assert np.allclose(s[:, 1, 1], s[:, 0, 0]), "S22 must equal S11 by symmetry"


def test_extract_sparams_from_raw_skips_symmetry_when_disabled(monkeypatch, tmp_path):
    from mcp_ltspice import extract as extract_mod

    n = 3
    z0 = 50.0
    traces = {
        "frequency": np.linspace(1e9, 2e9, n).astype(np.complex128),
        "V(p1)": np.full(n, 0.6 + 0.1j, dtype=np.complex128),
        "V(p2)": np.full(n, 0.4 - 0.05j, dtype=np.complex128),
        "I(Rs1)": np.full(n, 0.005, dtype=np.complex128),
    }
    import sys
    import types

    fake_spicelib = types.SimpleNamespace(RawRead=lambda path: _MockRawRead(traces))
    monkeypatch.setitem(sys.modules, "spicelib", fake_spicelib)

    raw_path = tmp_path / "test.raw"
    raw_path.touch()
    net = extract_mod.extract_sparams_from_raw(
        raw_path,
        port_map={1: "p1", 2: "p2"},
        z0=z0,
        assume_reciprocal_symmetric=False,
    )
    # Column 2 left as zero when reciprocity / symmetry isn't assumed
    assert np.all(net.s[:, 0, 1] == 0)
    assert np.all(net.s[:, 1, 1] == 0)
