"""Harmonic balance via Xyce (issue #24).

The headline test drives a *behavioural cubic* — ``V(out) = a1·V(in) +
a3·V(in)³`` — because that is the one nonlinearity whose third-order
intercept has a closed form:

    A_IIP3 = sqrt(4·a1 / (3·a3))

so IIP3 can be checked against an exact number rather than a plausible one.
A diode pair looks like the more realistic device but is a poor validator:
its I-V is exponential, not cubic, so its intermodulation does not follow a
3:1 slope at all and there is nothing exact to compare against.
"""

from __future__ import annotations

import itertools
import math

import numpy as np
import pytest

from mcp_qucs_s.harmonic_balance import (
    HBSpectrum,
    analyze,
    build_hb_netlist,
    dbm_to_source_amplitude,
    parse_hb_fd,
    sweep_compression,
    volts_to_dbm,
)

A1, A3 = 1.0, 10.0
CUBIC_DUT = ["Rin in 0 50", f"Bnl out 0 V={{{A1}*V(in) + {A3}*V(in)*V(in)*V(in)}}"]
A_IIP3 = math.sqrt(4.0 * A1 / (3.0 * A3))
IIP3_THEORY_DBM = 10.0 * math.log10((A_IIP3**2 / (2.0 * 50.0)) / 1e-3)

F1, F2 = 0.99e9, 1.01e9


# ---------------------------------------------------------------------------
# Pure helpers — no simulator needed
# ---------------------------------------------------------------------------


def test_available_power_conversion_round_trips() -> None:
    """P = V_peak^2/(8·Z0) for available power; a wrong factor shifts all dBm."""
    for dbm in (-30.0, -10.0, 0.0, 10.0):
        amp = dbm_to_source_amplitude(dbm, 50.0)
        # At a matched load the node sees half the source amplitude.
        assert volts_to_dbm(amp / 2.0, 50.0) == pytest.approx(dbm, abs=1e-9)


def test_0_dbm_is_the_familiar_632_mv() -> None:
    assert dbm_to_source_amplitude(0.0, 50.0) == pytest.approx(0.6324555, rel=1e-6)


def test_numfreq_gets_one_entry_per_tone() -> None:
    """Xyce aborts with a single NUMFREQ when two tones are given."""
    one = build_hb_netlist(CUBIC_DUT, fundamentals_hz=[1e9], harmonics=7, input_power_dbm=-20)
    two = build_hb_netlist(CUBIC_DUT, fundamentals_hz=[F1, F2], harmonics=7, input_power_dbm=-20)
    assert "NUMFREQ=7" in one and "NUMFREQ=7,7" not in one
    assert "NUMFREQ=7,7" in two


def test_two_tone_sources_are_in_series() -> None:
    text = build_hb_netlist(CUBIC_DUT, fundamentals_hz=[F1, F2], harmonics=5, input_power_dbm=-20)
    assert "Vs1 src n_tone" in text
    assert "Vs2 n_tone 0" in text


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"fundamentals_hz": [], "harmonics": 5}, "At least one fundamental"),
        ({"fundamentals_hz": [1e9, 2e9, 3e9], "harmonics": 5}, "Only single- and two-tone"),
        ({"fundamentals_hz": [1e9, 1e9], "harmonics": 5}, "identical"),
        ({"fundamentals_hz": [-1e9], "harmonics": 5}, "must be positive"),
        ({"fundamentals_hz": [1e9], "harmonics": 0}, "harmonics must be"),
    ],
)
def test_invalid_configurations_are_rejected(kwargs, match) -> None:
    with pytest.raises((ValueError, NotImplementedError), match=match):
        build_hb_netlist(CUBIC_DUT, input_power_dbm=-20, **kwargs)


def test_parser_folds_the_two_sided_spectrum(tmp_path) -> None:
    """Xyce prints negative frequencies too; amplitude doubles on folding."""
    prn = tmp_path / "x.HB.FD.prn"
    prn.write_text(
        "Index   FREQ   Re(V(OUT))   Im(V(OUT))\n"
        "0   -1.00000000e+09   0.25   0.0\n"
        "1    0.00000000e+00   0.10   0.0\n"
        "2    1.00000000e+09   0.25   0.0\n",
        encoding="utf-8",
    )
    spec = parse_hb_fd(prn)
    assert spec.freqs_hz.tolist() == [0.0, 1e9]
    v_dc, _ = spec.at(0.0)
    v_tone, _ = spec.at(1e9)
    assert v_dc == pytest.approx(0.10), "DC must not be doubled"
    assert v_tone == pytest.approx(0.50), "a positive-frequency bin folds to 2x"


def test_parser_rejects_an_empty_output(tmp_path) -> None:
    prn = tmp_path / "empty.HB.FD.prn"
    prn.write_text("Index   FREQ   Re(V(OUT))   Im(V(OUT))\n", encoding="utf-8")
    with pytest.raises(ValueError, match="No harmonic-balance data"):
        parse_hb_fd(prn)


def test_spectrum_lookup_picks_the_nearest_bin() -> None:
    spec = HBSpectrum(
        freqs_hz=np.array([1e9, 2e9]),
        volts_peak=np.array([1.0, 0.1]),
        dbm=np.array([10.0, -10.0]),
    )
    assert spec.at(1.01e9)[1] == pytest.approx(10.0)
    assert spec.at(1.9e9)[1] == pytest.approx(-10.0)


# ---------------------------------------------------------------------------
# Against real Xyce
# ---------------------------------------------------------------------------


@pytest.mark.xyce
@pytest.mark.integration
def test_iip3_matches_the_closed_form_cubic() -> None:
    """The core correctness claim, against an exactly-known intercept."""
    result = analyze(CUBIC_DUT, fundamentals_hz=[F1, F2], harmonics=5, input_power_dbm=-40.0)
    assert result.iip3_dbm is not None
    assert result.iip3_dbm == pytest.approx(IIP3_THEORY_DBM, abs=0.1), (
        f"IIP3 {result.iip3_dbm:.3f} dBm vs closed form {IIP3_THEORY_DBM:.3f} dBm"
    )


@pytest.mark.xyce
@pytest.mark.integration
def test_third_order_products_follow_a_3_to_1_slope() -> None:
    """IM3 must move 3 dB for every 1 dB of drive, well below compression."""
    low = analyze(CUBIC_DUT, fundamentals_hz=[F1, F2], harmonics=5, input_power_dbm=-40.0)
    high = analyze(CUBIC_DUT, fundamentals_hz=[F1, F2], harmonics=5, input_power_dbm=-30.0)
    assert low.im3_dbm is not None and high.im3_dbm is not None

    d_im3 = high.im3_dbm - low.im3_dbm
    assert d_im3 == pytest.approx(30.0, abs=0.5), f"IM3 moved {d_im3:.2f} dB per 10 dB"

    # And the extrapolated intercept must not drift with drive level.
    assert low.iip3_dbm == pytest.approx(high.iip3_dbm, abs=0.1)


@pytest.mark.xyce
@pytest.mark.integration
def test_im3_sidebands_are_symmetric() -> None:
    result = analyze(CUBIC_DUT, fundamentals_hz=[F1, F2], harmonics=5, input_power_dbm=-30.0)
    lo, hi = result.im3_freqs_hz
    assert lo == pytest.approx(2 * F1 - F2)
    assert hi == pytest.approx(2 * F2 - F1)
    assert result.spectrum.at(lo)[1] == pytest.approx(result.spectrum.at(hi)[1], abs=0.5)


@pytest.mark.xyce
@pytest.mark.integration
def test_single_tone_reports_harmonic_content() -> None:
    """One tone gives harmonic distortion; there is no IM3 to report."""
    result = analyze(CUBIC_DUT, fundamentals_hz=[1e9], harmonics=5, input_power_dbm=-20.0)
    assert result.im3_dbm is None
    assert result.fundamental_dbm[0] == pytest.approx(-20.0, abs=0.5)
    # A cubic generates a third harmonic but no even orders.
    third = result.spectrum.at(3e9)[1]
    second = result.spectrum.at(2e9)[1]
    assert third > second + 20.0, (
        f"a cubic should favour the 3rd harmonic ({third:.1f} dBm) over the 2nd ({second:.1f} dBm)"
    )


@pytest.mark.xyce
@pytest.mark.integration
def test_compression_sweep_finds_p1db() -> None:
    """A cubic with a negative third-order term compresses predictably."""
    dut = ["Rin in 0 50", "Bnl out 0 V={1.0*V(in) - 10.0*V(in)*V(in)*V(in)}"]
    data = sweep_compression(
        dut,
        fundamental_hz=1e9,
        input_powers_dbm=[-30.0, -20.0, -15.0, -10.0, -6.0, -3.0, 0.0],
        harmonics=5,
    )
    assert data["small_signal_gain_db"] == pytest.approx(0.0, abs=0.2)
    assert data["p1db_in_dbm"] is not None, (
        f"never reached 1 dB compression: {data['compression_db']}"
    )
    # Compression must be monotonic in drive for this well-behaved nonlinearity.
    comp = data["compression_db"]
    assert all(b >= a - 0.05 for a, b in itertools.pairwise(comp)), comp


@pytest.mark.xyce
@pytest.mark.integration
def test_mcp_tool_returns_intercepts_and_a_caveat() -> None:
    import mcp_qucs_s.server as S

    fn = getattr(S.run_harmonic_balance, "fn", S.run_harmonic_balance)
    env = fn(CUBIC_DUT, [F1, F2], harmonics=5, input_power_dbm=-30.0).model_dump()
    assert env["status"] == "ok", env["error"]
    assert env["data"]["iip3_dbm"] == pytest.approx(IIP3_THEORY_DBM, abs=0.2)
    assert env["warnings"], "single-point IIP3 extrapolation should carry a caveat"


def test_mcp_tool_reports_missing_xyce_usefully(monkeypatch) -> None:
    import mcp_qucs_s.server as S

    monkeypatch.setattr(S, "is_xyce_available", lambda: False)
    fn = getattr(S.run_harmonic_balance, "fn", S.run_harmonic_balance)
    env = fn(CUBIC_DUT, [F1, F2]).model_dump()
    assert env["status"] == "error"
    assert "installation.md" in env["error"]
