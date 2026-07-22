"""Frequency-grid policy for cascade / de-embed (issue #35).

``cascade_networks`` used :func:`numpy.intersect1d` to find a common grid,
which demands exact float equality. Two instruments essentially never emit
bit-identical grids, so real inputs fell into the fallback branch — "use
network 1's grid and interpolate the others" — which **extrapolates** the
other networks past the end of their measured data, silently.
``deembed_network`` raised in the same situation, and the inconsistency was
undocumented.

Both now restrict to the overlap of the measured ranges, so every output
point is interpolated from real data, and report any trimming.

The existing fixtures deliberately share a grid (see ``conftest.py``), so
this path was never exercised.
"""

from __future__ import annotations

import numpy as np
import pytest
import skrf as rf

from mcp_rf_analysis.network_ops import (
    cascade_networks,
    common_frequency_grid,
    deembed_network,
)
from rf_mcp_common.touchstone import write_touchstone


def _thru(path, f_start, f_stop, n, loss_db=0.5):
    """A simple attenuating thru on an arbitrary grid."""
    f = np.linspace(f_start, f_stop, n)
    s = np.zeros((n, 2, 2), dtype=complex)
    mag = 10 ** (-loss_db / 20.0)
    s[:, 1, 0] = mag
    s[:, 0, 1] = mag
    s[:, 0, 0] = 0.01
    s[:, 1, 1] = 0.01
    net = rf.Network(frequency=rf.Frequency.from_f(f, unit="Hz"), s=s, z0=50.0)
    return write_touchstone(net, path)


def _net(f_start, f_stop, n):
    f = np.linspace(f_start, f_stop, n)
    s = np.zeros((n, 2, 2), dtype=complex)
    s[:, 1, 0] = s[:, 0, 1] = 0.9
    return rf.Network(frequency=rf.Frequency.from_f(f, unit="Hz"), s=s, z0=50.0)


# ---------------------------------------------------------------------------
# The grid policy itself
# ---------------------------------------------------------------------------


def test_disjoint_ranges_raise_naming_the_spans() -> None:
    a, b = _net(1e9, 2e9, 101), _net(5e9, 6e9, 101)
    with pytest.raises(ValueError, match="do not overlap"):
        common_frequency_grid([a, b], ["a.s2p", "b.s2p"])


def test_grid_is_confined_to_the_overlap() -> None:
    a, b = _net(1e9, 3e9, 201), _net(2e9, 5e9, 301)
    grid = common_frequency_grid([a, b], ["a", "b"])
    assert grid.min() == pytest.approx(2e9)
    assert grid.max() == pytest.approx(3e9)


def test_never_extrapolates_beyond_any_input() -> None:
    """Every output point must sit inside every input's measured range."""
    nets = [_net(1e9, 3e9, 201), _net(2e9, 5e9, 301), _net(1.5e9, 4e9, 151)]
    grid = common_frequency_grid(nets, ["a", "b", "c"])
    for n in nets:
        assert grid.min() >= n.f.min() - 1e-6
        assert grid.max() <= n.f.max() + 1e-6


def test_trimming_is_reported() -> None:
    notes: list[str] = []
    common_frequency_grid(
        [_net(1e9, 3e9, 201), _net(2e9, 5e9, 301)], ["a.s2p", "b.s2p"], collect_warnings=notes
    )
    assert notes, "trimming an input's range must not be silent"
    assert any("2-3 GHz" in n or "2-3" in n for n in notes), notes


def test_identical_grids_produce_no_warning() -> None:
    notes: list[str] = []
    common_frequency_grid(
        [_net(1e9, 3e9, 201), _net(1e9, 3e9, 201)], ["a", "b"], collect_warnings=notes
    )
    assert notes == []


def test_keeps_the_densest_available_resolution() -> None:
    """A coarse network must not throw away a fine one's resolution."""
    coarse, fine = _net(1e9, 3e9, 11), _net(1e9, 3e9, 401)
    grid = common_frequency_grid([coarse, fine], ["coarse", "fine"])
    assert grid.size > 100, f"resolution collapsed to {grid.size} points"


# ---------------------------------------------------------------------------
# Through the public operations
# ---------------------------------------------------------------------------


def test_cascade_on_partially_overlapping_grids_covers_only_the_overlap(tmp_path) -> None:
    a = _thru(tmp_path / "a.s2p", 1e9, 3e9, 201)
    b = _thru(tmp_path / "b.s2p", 2e9, 5e9, 301)
    notes: list[str] = []
    out = cascade_networks([a, b], tmp_path / "out.s2p", collect_warnings=notes)

    result = rf.Network(str(out))
    assert result.f.min() == pytest.approx(2e9)
    assert result.f.max() == pytest.approx(3e9)
    assert notes


def test_cascade_on_disjoint_grids_raises(tmp_path) -> None:
    a = _thru(tmp_path / "a.s2p", 1e9, 2e9, 101)
    b = _thru(tmp_path / "b.s2p", 5e9, 6e9, 101)
    with pytest.raises(ValueError, match="do not overlap"):
        cascade_networks([a, b], tmp_path / "out.s2p")


def test_cascade_on_offset_grids_no_longer_extrapolates(tmp_path) -> None:
    """The real-world case: same nominal span, grids offset by a hair.

    intersect1d found fewer than two exact matches here, so the old code
    silently extrapolated b onto a's grid.
    """
    a = _thru(tmp_path / "a.s2p", 1.0e9, 3.0e9, 201)
    b = _thru(tmp_path / "b.s2p", 1.0e9 + 137.0, 3.0e9 - 91.0, 199)
    out = cascade_networks([a, b], tmp_path / "out.s2p")
    result = rf.Network(str(out))
    assert result.f.min() >= 1.0e9 + 137.0 - 1e-6
    assert result.f.max() <= 3.0e9 - 91.0 + 1e-6

    # Two cascaded 0.5 dB thrus give 1.0 dB; extrapolated garbage would not.
    s21_db = 20.0 * np.log10(np.abs(result.s[:, 1, 0]))
    assert np.allclose(s21_db, -1.0, atol=0.05), f"got {s21_db.min():.2f}..{s21_db.max():.2f} dB"


def test_deembed_still_raises_on_disjoint_grids(tmp_path) -> None:
    meas = _thru(tmp_path / "m.s2p", 1e9, 2e9, 101)
    fix = _thru(tmp_path / "f.s2p", 5e9, 6e9, 101)
    with pytest.raises(ValueError, match="do not overlap"):
        deembed_network(meas, fix, tmp_path / "out.s2p")


def test_deembed_reports_trimming(tmp_path) -> None:
    meas = _thru(tmp_path / "m.s2p", 1e9, 4e9, 301)
    fix = _thru(tmp_path / "f.s2p", 2e9, 3e9, 101, loss_db=0.1)
    notes: list[str] = []
    out = deembed_network(meas, fix, tmp_path / "out.s2p", collect_warnings=notes)
    result = rf.Network(str(out))
    assert result.f.min() == pytest.approx(2e9)
    assert result.f.max() == pytest.approx(3e9)
    assert notes


def test_cascade_tool_surfaces_warnings_in_the_envelope(tmp_path) -> None:
    import mcp_rf_analysis.server as S

    a = _thru(tmp_path / "a.s2p", 1e9, 3e9, 201)
    b = _thru(tmp_path / "b.s2p", 2e9, 5e9, 301)
    fn = getattr(S.cascade_networks, "fn", S.cascade_networks)
    env = fn([str(a), str(b)], str(tmp_path / "out.s2p")).model_dump()
    assert env["status"] == "ok", env["error"]
    assert env["warnings"], "the envelope should carry the trimming warning"
    assert "GHz" in env["warnings"][0]
