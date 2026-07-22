"""Tests for Qucs-S .dat parsing and Touchstone conversion.

``sparams.py`` had no test coverage at all, despite being the only code
that runs once a user actually installs Qucs-S: both ``run_sp_analysis``
and ``export_touchstone`` funnel through ``dat_to_touchstone``. It needs
no binary to test — the input is a text file.
"""

from __future__ import annotations

import numpy as np
import pytest

from mcp_qucs_s.sparams import dat_to_touchstone, network_from_dat, parse_qucs_dat
from rf_mcp_common.touchstone import read_touchstone


def test_parse_recovers_frequency_grid(qucs_dat) -> None:
    data = parse_qucs_dat(qucs_dat)
    np.testing.assert_allclose(data["frequency"], [1e9, 2e9, 3e9])


def test_parse_recovers_every_s_component(qucs_dat) -> None:
    data = parse_qucs_dat(qucs_dat)
    for i in (1, 2):
        for j in (1, 2):
            assert f"S[{i},{j}].r" in data
            assert f"S[{i},{j}].i" in data
    np.testing.assert_allclose(data["S[2,1].r"], [0.9, 0.8, 0.7])
    np.testing.assert_allclose(data["S[1,1].i"], [-0.01, -0.02, -0.03])


def test_dat_to_touchstone_round_trips(qucs_dat, tmp_path) -> None:
    """The written .s2p must read back with the same complex values."""
    out = dat_to_touchstone(qucs_dat, tmp_path / "out.s2p")
    net = read_touchstone(out)
    np.testing.assert_allclose(net.f, [1e9, 2e9, 3e9])
    np.testing.assert_allclose(net.s[:, 0, 0], [0.1 - 0.01j, 0.2 - 0.02j, 0.3 - 0.03j], atol=1e-9)
    np.testing.assert_allclose(net.s[:, 1, 0], [0.9 + 0.05j, 0.8 + 0.06j, 0.7 + 0.07j], atol=1e-9)


def test_network_from_dat_matches_touchstone_path(qucs_dat, tmp_path) -> None:
    """The in-memory loader and the on-disk one must not diverge."""
    direct = network_from_dat(qucs_dat)
    via_disk = read_touchstone(dat_to_touchstone(qucs_dat, tmp_path / "out.s2p"))
    np.testing.assert_allclose(direct.s, via_disk.s, atol=1e-9)
    np.testing.assert_allclose(direct.f, via_disk.f)


def test_missing_frequency_raises_named_error(tmp_path) -> None:
    path = tmp_path / "nofreq.dat"
    path.write_text("<dep S[1,1].r dep frequency>\n  0.1\n</dep>\n", encoding="utf-8")
    with pytest.raises(ValueError, match="No 'frequency' variable"):
        dat_to_touchstone(path, tmp_path / "out.s2p")


def test_truncated_dat_raises_instead_of_keyerror(qucs_dat, tmp_path) -> None:
    """A .dat missing an S component must name it, not raise a bare KeyError.

    This is the realistic partial-output case: Qucs-S interrupted, or a
    schematic that only saved some ports.
    """
    text = qucs_dat.read_text(encoding="utf-8")
    truncated = text.split("<dep S[2,1].r")[0]
    path = tmp_path / "partial.dat"
    path.write_text(truncated, encoding="utf-8")

    with pytest.raises(ValueError, match=r"Missing S\[2,1\]"):
        dat_to_touchstone(path, tmp_path / "out.s2p")
    # network_from_dat previously skipped this validation entirely.
    with pytest.raises(ValueError, match=r"Missing S\[2,1\]"):
        network_from_dat(path)


def test_ragged_dat_raises(qucs_dat, tmp_path) -> None:
    """A component with fewer points than the sweep must not broadcast."""
    text = qucs_dat.read_text(encoding="utf-8")
    ragged = text.replace(
        "<dep S[2,2].i dep frequency>\n  -0.02\n  -0.03\n  -0.04\n</dep>",
        "<dep S[2,2].i dep frequency>\n  -0.02\n</dep>",
    )
    path = tmp_path / "ragged.dat"
    path.write_text(ragged, encoding="utf-8")
    with pytest.raises(ValueError, match="points but frequency has"):
        dat_to_touchstone(path, tmp_path / "out.s2p")
