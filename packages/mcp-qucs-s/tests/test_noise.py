"""Qucs-S noise-parameter extraction (issue #25).

The anchor is a fact with no fitting in it: **a passive network's noise
figure equals its insertion loss**, exactly, at the IEEE reference
temperature T0 = 290 K. So a matched 10 dB attenuator must read NF = 10.000
dB and Fmin = NF, with Γopt = 0.

That only holds at 290 K. Qucs defaults components to 26.85 °C = 300 K,
where the same pad reads 10.13 dB — which is *also* exactly right, since
F = 1 + (L−1)·T/T0. Both are checked, because a plausible-looking noise
figure is the easiest thing in the world to produce by accident.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from mcp_qucs_s.noise import (
    T0_CELSIUS,
    NoiseParameters,
    analyze_noise,
    build_noise_netlist,
    parse_noise_parameters,
)
from mcp_qucs_s.runner import find_qucs_s

HAS_QUCS = find_qucs_s() is not None
requires_qucs = pytest.mark.skipif(not HAS_QUCS, reason="qucsator not installed")

Z0 = 50.0


def tee_pad(attenuation_db: float, z0: float = Z0) -> list[str]:
    """Matched resistive tee attenuator of the given loss."""
    n = 10 ** (attenuation_db / 20.0)
    r_series = z0 * (n - 1) / (n + 1)
    r_shunt = z0 * 2 * n / (n * n - 1)
    return [
        f'R:R1 _p1 _m R="{r_series:.9f}"',
        f'R:R2 _m gnd R="{r_shunt:.9f}"',
        f'R:R3 _m _p2 R="{r_series:.9f}"',
    ]


ASYMMETRIC = ['R:R1 _p1 _p2 R="20"', 'R:R2 _p2 gnd R="100"']


# ---------------------------------------------------------------------------
# Netlist construction and parsing — no simulator needed
# ---------------------------------------------------------------------------


def test_noise_analysis_is_requested() -> None:
    text = build_noise_netlist(ASYMMETRIC, f_start_hz=1e9, f_stop_hz=2e9)
    assert 'Noise="yes"' in text


def test_reference_temperature_is_applied_to_resistors() -> None:
    """Without this a passive network's NF does not equal its loss."""
    text = build_noise_netlist(ASYMMETRIC, f_start_hz=1e9, f_stop_hz=2e9)
    for line in text.splitlines():
        if line.startswith("R:"):
            assert 'Temp="16.85"' in line, line


def test_explicit_component_temperature_is_not_overridden() -> None:
    dut = ['R:R1 _p1 _p2 R="20" Temp="85"']
    text = build_noise_netlist(dut, f_start_hz=1e9, f_stop_hz=2e9)
    assert 'Temp="85"' in text
    assert 'Temp="16.85"' not in text


def test_temperature_injection_can_be_disabled() -> None:
    text = build_noise_netlist(ASYMMETRIC, f_start_hz=1e9, f_stop_hz=2e9, temp_c=None)
    assert "Temp=" not in text


def test_non_resistor_lines_are_left_alone() -> None:
    """Blindly rewriting arbitrary device cards would be worse than not trying."""
    dut = ['R:R1 _p1 _n R="20"', 'C:C1 _n _p2 C="1p"']
    text = build_noise_netlist(dut, f_start_hz=1e9, f_stop_hz=2e9)
    cap = next(ln for ln in text.splitlines() if ln.startswith("C:"))
    assert "Temp=" not in cap


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"f_start_hz": 2e9, "f_stop_hz": 1e9}, "must exceed"),
        ({"f_start_hz": 1e9, "f_stop_hz": 2e9, "points": 0}, "points must be"),
        ({"f_start_hz": 1e9, "f_stop_hz": 2e9, "z0": 0.0}, "z0 must be positive"),
    ],
)
def test_invalid_sweeps_are_rejected(kwargs, match) -> None:
    with pytest.raises(ValueError, match=match):
        build_noise_netlist(ASYMMETRIC, **kwargs)


def test_empty_circuit_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty circuit"):
        build_noise_netlist([], f_start_hz=1e9, f_stop_hz=2e9)


def test_dataset_without_noise_block_is_reported(tmp_path) -> None:
    dat = tmp_path / "sp_only.dat"
    dat.write_text(
        "<Qucs Dataset 1.0.7>\n<indep frequency 1>\n  +1.0e+09\n</indep>\n"
        "<dep S[1,1] frequency>\n  +0.5-j0.5\n</dep>\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r'Noise="yes"'):
        parse_noise_parameters(dat)


def test_source_gamma_must_be_passive() -> None:
    params = NoiseParameters(
        freq_hz=np.array([1e9]),
        nf_db=np.array([3.0]),
        nfmin_db=np.array([2.0]),
        gamma_opt=np.array([0.2 + 0.1j]),
        rn_ohm=np.array([25.0]),
    )
    with pytest.raises(ValueError, match="must be < 1"):
        params.nf_db_at_source(1.2 + 0j)


def test_nf_at_gamma_opt_returns_fmin() -> None:
    """By definition the optimum source gives exactly Fmin."""
    params = NoiseParameters(
        freq_hz=np.array([1e9]),
        nf_db=np.array([3.0]),
        nfmin_db=np.array([2.0]),
        gamma_opt=np.array([0.3 + 0.2j]),
        rn_ohm=np.array([25.0]),
    )
    assert params.nf_db_at_source(0.3 + 0.2j)[0] == pytest.approx(2.0, abs=1e-12)


# ---------------------------------------------------------------------------
# Against real qucsator
# ---------------------------------------------------------------------------


@requires_qucs
@pytest.mark.qucs
@pytest.mark.integration
@pytest.mark.parametrize("loss_db", [3.0, 6.0, 10.0, 20.0])
def test_passive_pad_noise_figure_equals_its_loss(loss_db: float) -> None:
    """The anchor: NF = insertion loss, exactly, at 290 K."""
    params = analyze_noise(
        tee_pad(loss_db), f_start_hz=1e9, f_stop_hz=2e9, points=3, temp_c=T0_CELSIUS
    )
    assert np.allclose(params.nf_db, loss_db, atol=1e-4), (
        f"{loss_db} dB pad reported NF {params.nf_db[0]:.6f} dB"
    )
    # A matched symmetric pad is its own optimum source.
    assert np.allclose(params.nfmin_db, params.nf_db, atol=1e-9)
    assert np.allclose(np.abs(params.gamma_opt), 0.0, atol=1e-9)


@requires_qucs
@pytest.mark.qucs
@pytest.mark.integration
def test_noise_figure_follows_physical_temperature() -> None:
    """At Qucs's 26.85 C default a 10 dB pad reads 10.13 dB, not 10.00.

    F = 1 + (L-1)·T/T0. Reading 10.00 dB here would mean the temperature
    was being ignored.
    """
    params = analyze_noise(tee_pad(10.0), f_start_hz=1e9, f_stop_hz=2e9, points=3, temp_c=26.85)
    t_over_t0 = (26.85 + 273.15) / (T0_CELSIUS + 273.15)
    expected = 10.0 * math.log10(1.0 + (10.0 - 1.0) * t_over_t0)
    assert params.nf_db[0] == pytest.approx(expected, abs=1e-3), (
        f"got {params.nf_db[0]:.6f} dB, expected {expected:.6f} dB"
    )


@requires_qucs
@pytest.mark.qucs
@pytest.mark.integration
def test_rn_is_in_ohms_not_normalised() -> None:
    """Self-consistency pins the convention.

    F(Γs=0) computed from Fmin, Rn and Γopt must reproduce the reported
    NF50. With Rn in ohms this holds to machine precision; treating it as
    normalised to Z0 is off by ~0.01 in noise factor.
    """
    params = analyze_noise(ASYMMETRIC, f_start_hz=1e9, f_stop_hz=2e9, points=3)
    recomputed = params.nf_db_at_source(0.0 + 0.0j)
    assert np.allclose(recomputed, params.nf_db, atol=1e-9), (
        f"recomputed {recomputed[0]:.9f} dB vs reported {params.nf_db[0]:.9f} dB"
    )


@requires_qucs
@pytest.mark.qucs
@pytest.mark.integration
def test_asymmetric_network_has_a_nonzero_gamma_opt() -> None:
    """Otherwise the Γopt column could be a stub of zeros and go unnoticed."""
    params = analyze_noise(ASYMMETRIC, f_start_hz=1e9, f_stop_hz=2e9, points=3)
    assert np.all(np.abs(params.gamma_opt) > 1e-6)
    assert np.all(params.nfmin_db <= params.nf_db + 1e-9), (
        "Fmin must not exceed the noise figure at the reference impedance"
    )


@requires_qucs
@pytest.mark.qucs
@pytest.mark.integration
def test_mismatching_the_source_can_only_raise_noise_figure() -> None:
    params = analyze_noise(ASYMMETRIC, f_start_hz=1e9, f_stop_hz=2e9, points=3)
    for gamma in (0.0 + 0j, 0.3 + 0.2j, -0.5 + 0.1j, 0.8j):
        nf = params.nf_db_at_source(gamma)
        assert np.all(nf >= params.nfmin_db - 1e-9), (
            f"Γs={gamma} gave NF below Fmin, which is impossible"
        )


@requires_qucs
@pytest.mark.qucs
@pytest.mark.integration
def test_mcp_tool_returns_rows_and_source_evaluation(tmp_path) -> None:
    import mcp_qucs_s.server as S

    fn = getattr(S.extract_noise_parameters, "fn", S.extract_noise_parameters)
    env = fn(
        tee_pad(10.0),
        1e9,
        2e9,
        points=3,
        source_gamma_real=0.2,
        source_gamma_imag=0.1,
    ).model_dump()

    assert env["status"] == "ok", env["error"]
    rows = env["data"]["parameters"]
    assert len(rows) == 3
    assert rows[0]["nf_db"] == pytest.approx(10.0, abs=1e-4)
    assert set(rows[0]) >= {"freq_hz", "nf_db", "nfmin_db", "gamma_opt_mag", "rn_ohm"}
    assert len(env["data"]["nf_db_at_source"]) == 3
    # Mismatching a pad whose Γopt is 0 must cost noise figure.
    assert env["data"]["nf_db_at_source"][0] > rows[0]["nf_db"]


def test_mcp_tool_reports_missing_qucs(monkeypatch) -> None:
    import mcp_qucs_s.server as S

    monkeypatch.setattr(S, "is_qucs_available", lambda: False)
    fn = getattr(S.extract_noise_parameters, "fn", S.extract_noise_parameters)
    env = fn(ASYMMETRIC, 1e9, 2e9).model_dump()
    assert env["status"] == "error"
    assert "installation.md" in env["error"]
