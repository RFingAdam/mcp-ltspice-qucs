"""Hz-strict Touchstone read / write helpers built on scikit-rf.

Internal frequency unit is always Hz. We surface this contract by
returning (freq_hz, s_complex) tuples instead of trusting consumers to
read scikit-rf's Frequency object correctly.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import skrf as rf
from numpy.typing import NDArray


def read_touchstone(path: str | Path) -> rf.Network:
    """Read a Touchstone file into an skrf Network. Path is resolved to absolute."""
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Touchstone file not found: {p}")
    return rf.Network(str(p))


def write_touchstone(
    network: rf.Network,
    path: str | Path,
    *,
    form: str = "ri",
) -> Path:
    """Write an skrf Network to a Touchstone file.

    ``form`` is one of ``"ri"`` (real/imaginary), ``"ma"`` (mag/angle),
    or ``"db"`` (dB/angle).
    """
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    network.write_touchstone(str(p.with_suffix("")), form=form)
    # skrf appends the appropriate .sNp extension based on nports
    return p.parent / f"{p.stem}.s{network.nports}p"


def network_to_touchstone(
    freq_hz: NDArray[np.float64],
    s: NDArray[np.complex128],
    path: str | Path,
    *,
    z0: float = 50.0,
    name: str | None = None,
    form: str = "ri",
) -> Path:
    """Build an skrf Network from raw arrays and write it to disk."""
    if freq_hz.ndim != 1:
        raise ValueError(f"freq_hz must be 1-D, got shape {freq_hz.shape}")
    if s.ndim != 3:
        raise ValueError(f"s must be (npoints, nports, nports), got shape {s.shape}")
    if s.shape[0] != freq_hz.size:
        raise ValueError(f"s.shape[0]={s.shape[0]} does not match freq_hz.size={freq_hz.size}")
    if s.shape[1] != s.shape[2]:
        raise ValueError(f"s must be square in port axes, got {s.shape}")

    freq = rf.Frequency.from_f(freq_hz, unit="Hz")
    net = rf.Network(frequency=freq, s=s, z0=z0, name=name or Path(path).stem)
    return write_touchstone(net, path, form=form)


def sparams_at(
    network: rf.Network,
    freq_hz: float,
    *,
    interp: bool = True,
) -> NDArray[np.complex128]:
    """Return the S-matrix at a single frequency.

    If ``freq_hz`` is not in the sweep and ``interp=True``, linearly
    interpolate. Otherwise raise ``ValueError``.
    """
    f = network.f  # already in Hz
    if freq_hz < f.min() or freq_hz > f.max():
        raise ValueError(f"freq_hz={freq_hz} outside sweep [{f.min()}, {f.max()}]")
    if not interp:
        idx = int(np.argmin(np.abs(f - freq_hz)))
        if not np.isclose(f[idx], freq_hz):
            raise ValueError(f"freq_hz={freq_hz} not in sweep and interp=False")
        return np.asarray(network.s[idx])

    nports = network.nports
    out = np.zeros((nports, nports), dtype=np.complex128)
    for i in range(nports):
        for j in range(nports):
            out[i, j] = np.interp(freq_hz, f, network.s[:, i, j])
    return out
