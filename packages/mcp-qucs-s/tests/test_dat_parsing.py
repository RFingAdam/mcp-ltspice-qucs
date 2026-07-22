"""Qucs-S ``.dat`` parsing, tested against real qucsator-RF output.

The fixture in ``fixtures/butterworth_lpf_o3.dat`` is genuine output from
qucsator-RF 1.0.7 for the netlist beside it, so these tests cannot drift
away from the format the simulator actually writes — which is how the
original parser came to be wrong.

It expected each S-parameter split into ``S[1,1].r`` and ``S[1,1].i``
real-valued sections. Qucs writes one ``S[1,1]`` section holding complex
values in ``<real><sign>j<imag>`` form::

    +9.79999130152703123997e-01-j1.98999255443123357345e-01

so ``float(line)`` raised ``ValueError`` on every real file and no Qucs
simulation could ever be read back.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mcp_qucs_s.sparams import dat_to_touchstone, network_from_dat, parse_qucs_dat

FIXTURES = Path(__file__).parent / "fixtures"
REAL_DAT = FIXTURES / "butterworth_lpf_o3.dat"


def test_parses_real_qucsator_output() -> None:
    data = parse_qucs_dat(REAL_DAT)
    assert "frequency" in data
    assert data["frequency"].size == 21
    for key in ("S[1,1]", "S[2,1]", "S[1,2]", "S[2,2]"):
        assert key in data, f"missing {key}; got {sorted(data)}"
        assert data[key].size == 21


def test_complex_values_are_parsed_not_truncated() -> None:
    """The imaginary part must survive; a real-only parse looks plausible."""
    data = parse_qucs_dat(REAL_DAT)
    s21 = data["S[2,1]"]
    assert np.iscomplexobj(s21)
    # First point of the fixture, transcribed from the file by hand.
    assert s21[0].real == pytest.approx(9.79999130152703123997e-01, rel=1e-12)
    assert s21[0].imag == pytest.approx(-1.98999255443123357345e-01, rel=1e-12)
    assert np.any(np.abs(s21.imag) > 1e-6), "all imaginary parts vanished"


def test_frequency_axis_stays_real() -> None:
    data = parse_qucs_dat(REAL_DAT)
    f = data["frequency"]
    assert f[0] == pytest.approx(1e8)
    assert f[-1] == pytest.approx(4e9, rel=1e-6)
    assert np.all(np.diff(f) > 0), "frequency axis should be ascending"


def test_network_from_dat_matches_butterworth_theory() -> None:
    """End-to-end readback: real simulator output vs the closed form.

    |S21|^2 = 1 / (1 + (f/fc)^(2n)) for a doubly-terminated Butterworth.
    """
    net = network_from_dat(REAL_DAT)
    assert net.s.shape == (21, 2, 2)

    fc, order = 1e9, 3
    s21_db = 20.0 * np.log10(np.abs(net.s[:, 1, 0]))
    expected = -10.0 * np.log10(1.0 + (net.f / fc) ** (2 * order))
    worst = float(np.max(np.abs(s21_db - expected)))
    assert worst < 0.1, f"|S21| deviates from theory by {worst:.3f} dB"


def test_passband_is_well_matched() -> None:
    """A disconnected or mis-parsed network sits near |S11| = 0 dB."""
    net = network_from_dat(REAL_DAT)
    s11_db = 20.0 * np.log10(np.abs(net.s[:, 0, 0]))
    assert s11_db[0] < -20.0, f"expected a good low-frequency match, got {s11_db[0]:.1f} dB"


def test_reciprocity_holds_for_a_passive_ladder() -> None:
    net = network_from_dat(REAL_DAT)
    assert np.allclose(net.s[:, 1, 0], net.s[:, 0, 1], rtol=1e-9, atol=1e-12)


def test_dat_to_touchstone_roundtrip(tmp_path) -> None:
    import skrf as rf

    out = dat_to_touchstone(REAL_DAT, tmp_path / "out.s2p")
    assert out.is_file()
    reloaded = rf.Network(str(out))
    direct = network_from_dat(REAL_DAT)
    assert np.allclose(reloaded.s, direct.s, rtol=1e-6, atol=1e-9)


def test_split_real_imag_sections_still_parse(tmp_path) -> None:
    """Older Qucs builds split into .r/.i sections; keep reading those."""
    dat = tmp_path / "split.dat"
    dat.write_text(
        "<Qucs Dataset 1.0.0>\n"
        "<indep frequency 2>\n  +1.0e+09\n  +2.0e+09\n</indep>\n"
        "<dep S[1,1].r frequency>\n  +0.5\n  +0.25\n</dep>\n"
        "<dep S[1,1].i frequency>\n  -0.5\n  -0.25\n</dep>\n",
        encoding="utf-8",
    )
    data = parse_qucs_dat(dat)
    assert data["S[1,1].r"][0] == pytest.approx(0.5)
    assert data["S[1,1].i"][1] == pytest.approx(-0.25)


def test_missing_sparameter_names_the_file(tmp_path) -> None:
    dat = tmp_path / "partial.dat"
    dat.write_text(
        "<Qucs Dataset 1.0.7>\n<indep frequency 1>\n  +1.0e+09\n</indep>\n"
        "<dep S[1,1] frequency>\n  +0.5-j0.5\n</dep>\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"partial\.dat"):
        network_from_dat(dat)
