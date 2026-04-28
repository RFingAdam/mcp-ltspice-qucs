"""Regression tests for elliptic synthesis: the reported
`transmission_zeros_hz` must match the actual `1/(2π√(L_k C_k))` of the
synthesised trap pairs.

Background: prior versions of `_fit_lc_to_prototype` did a free
least-squares fit over (L, C) trap pairs without constraining
`L · C = 1/ω_zk²`. The fit's L/C product drifted, so the achieved
trap resonance no longer matched the reported transmission-zero
frequencies. The fix pins each trap's `C` to a derived
`C = 1/(ω_zk² · L)` so the LSQ optimises only over `L_trap` and the
constraint is honoured exactly.
"""

from __future__ import annotations

import math

import pytest

from mcp_ltspice.synthesis import Topology, synthesize_lc_lpf


def _trap_resonance_hz(L: float, C: float) -> float:
    return 1.0 / (2.0 * math.pi * math.sqrt(L * C))


@pytest.mark.parametrize("order", [3, 5, 7, 9])
@pytest.mark.parametrize(
    ("fc", "ripple", "stopband"),
    [
        (1.0e9, 0.1, 30.0),  # mild stopband
        (928e6, 0.1, 55.0),  # the spec where the bug was first observed
        (2.4e9, 0.05, 60.0),  # tighter ripple, deeper stopband
        (100e6, 0.5, 40.0),  # lower freq + higher ripple
    ],
)
def test_reported_tz_matches_achieved_resonance(
    order: int, fc: float, ripple: float, stopband: float
):
    """For every trap k in the synthesised ladder, the achieved
    `1/(2π√(L_k C_k))` must equal the reported `transmission_zeros_hz[k]`
    to within 1 % at every (order, fc, ripple, stopband) combination."""
    d = synthesize_lc_lpf(
        "elliptic",
        order=order,
        cutoff_hz=fc,
        ripple_db=ripple,
        stopband_atten_db=stopband,
        z0=50.0,
        topology=Topology.SERIES_FIRST,
    )
    reported = sorted(d.transmission_zeros_hz)
    achieved = []
    for k in range(2, order, 2):
        l_key, c_key = f"L{k}", f"C{k}"
        if l_key in d.components and c_key in d.components:
            achieved.append(_trap_resonance_hz(d.components[l_key], d.components[c_key]))
    achieved.sort()

    assert len(reported) == len(achieved), (
        f"Number of reported TZs ({len(reported)}) does not match number of "
        f"trap pairs in components ({len(achieved)}); something is wrong with "
        f"the synthesis output."
    )
    for r, a in zip(reported, achieved, strict=True):
        rel_err = abs(r - a) / r
        assert rel_err < 0.01, (
            f"Reported TZ {r / 1e6:.2f} MHz disagrees with achieved trap "
            f"resonance {a / 1e6:.2f} MHz by {rel_err * 100:.3f} %."
        )


def test_high_order_no_drift():
    """At higher orders (more traps) the fit has more freedom; verify the
    constraint still holds exactly."""
    d = synthesize_lc_lpf(
        "elliptic",
        order=9,
        cutoff_hz=1.0e9,
        ripple_db=0.1,
        stopband_atten_db=50,
        z0=50.0,
        topology=Topology.SERIES_FIRST,
    )
    reported = sorted(d.transmission_zeros_hz)
    achieved = []
    for k in (2, 4, 6, 8):
        achieved.append(_trap_resonance_hz(d.components[f"L{k}"], d.components[f"C{k}"]))
    achieved.sort()
    for r, a in zip(reported, achieved, strict=True):
        assert abs(r - a) / r < 1e-3, (
            f"At order 9: reported {r / 1e6:.2f} MHz vs achieved {a / 1e6:.2f} MHz"
        )
