"""S-parameter extraction from Qucs-S simulation output.

Qucs-S native ``.SP`` analysis emits a ``.dat`` file with named
variables that this module parses into an skrf Network and writes as a
Touchstone file.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import skrf as rf

from rf_mcp_common.touchstone import network_to_touchstone


def parse_qucs_dat(dat_path: str | Path) -> dict[str, np.ndarray]:
    """Parse a Qucs-S .dat output file into a dict of variable arrays.

    The .dat format is text with sections like::

        <indep frequency 101>
            900000000
            ...
        </indep>
        <dep S[1,1].r dep frequency>
            ...
        </dep>
        <dep S[1,1].i dep frequency>
            ...
        </dep>
    """
    text = Path(dat_path).read_text(encoding="utf-8", errors="replace")
    out: dict[str, np.ndarray] = {}

    for match in re.finditer(r"<(indep|dep)\s+([^\s>]+)[^>]*>(.*?)</\1>", text, re.DOTALL):
        name = match.group(2)
        body = match.group(3).strip()
        values = [float(line.strip()) for line in body.splitlines() if line.strip()]
        out[name] = np.asarray(values)
    return out


def _freq_and_s_matrix(
    data: dict[str, np.ndarray], dat_path: str | Path, nports: int
) -> tuple[np.ndarray, np.ndarray]:
    """Assemble (freq, S) from parsed .dat variables, validating as we go.

    Shared by both loaders so they report the same diagnostic. A partial
    .dat — Qucs-S was interrupted, or the schematic only saved some
    ports — otherwise surfaced as a bare KeyError naming an internal
    string, with no hint of which file was short.
    """
    if "frequency" not in data:
        raise ValueError(f"No 'frequency' variable in {dat_path}")
    freq_hz = data["frequency"]
    s = np.zeros((freq_hz.size, nports, nports), dtype=np.complex128)
    for i in range(nports):
        for j in range(nports):
            re_key = f"S[{i + 1},{j + 1}].r"
            im_key = f"S[{i + 1},{j + 1}].i"
            if re_key not in data or im_key not in data:
                raise ValueError(f"Missing S[{i + 1},{j + 1}] components in {dat_path}")
            if data[re_key].size != freq_hz.size or data[im_key].size != freq_hz.size:
                raise ValueError(
                    f"S[{i + 1},{j + 1}] in {dat_path} has "
                    f"{data[re_key].size} points but frequency has {freq_hz.size}"
                )
            s[:, i, j] = data[re_key] + 1j * data[im_key]
    return freq_hz, s


def dat_to_touchstone(
    dat_path: str | Path,
    output_s2p: str | Path,
    *,
    nports: int = 2,
    z0: float = 50.0,
) -> Path:
    """Convert a Qucs-S .dat file containing S-parameter results to Touchstone."""
    freq_hz, s = _freq_and_s_matrix(parse_qucs_dat(dat_path), dat_path, nports)
    return network_to_touchstone(freq_hz, s, output_s2p, z0=z0)


def network_from_dat(dat_path: str | Path, *, nports: int = 2, z0: float = 50.0) -> rf.Network:
    """Load a Qucs-S .dat directly into an skrf Network without going via disk."""
    freq_hz, s = _freq_and_s_matrix(parse_qucs_dat(dat_path), dat_path, nports)
    return rf.Network(
        frequency=rf.Frequency.from_f(freq_hz, unit="Hz"),
        s=s,
        z0=z0,
        name=Path(dat_path).stem,
    )
