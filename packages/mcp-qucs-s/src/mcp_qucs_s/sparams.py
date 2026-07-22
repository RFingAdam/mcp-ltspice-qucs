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

# Qucs writes complex numbers as <real><sign>j<imag>, e.g.
#   +9.7999913015270312e-01-j1.9899925544312335e-01
# Note the sign belongs to the imaginary part and `j` precedes its
# magnitude, so this is not a form Python's complex() accepts.
_QUCS_COMPLEX_RE = re.compile(
    r"^\s*(?P<re>[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)"
    r"(?P<sign>[+-])j(?P<im>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*$"
)


def _parse_qucs_number(token: str, var_name: str, dat_path: str | Path) -> complex | float:
    """Parse one Qucs data value, real or complex.

    Raises with the variable and file named rather than letting a bare
    ``ValueError: could not convert string to float`` escape — which is
    exactly what a real ``.dat`` used to produce, giving no clue that the
    parser simply did not understand the format.
    """
    m = _QUCS_COMPLEX_RE.match(token)
    if m is not None:
        imag = float(m.group("im"))
        if m.group("sign") == "-":
            imag = -imag
        return complex(float(m.group("re")), imag)
    try:
        return float(token)
    except ValueError:
        raise ValueError(
            f"Could not parse value {token!r} for variable {var_name!r} in {dat_path}. "
            "Expected a real number or Qucs complex notation like "
            "'+1.0e-01-j2.0e-02'."
        ) from None


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
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        values = [_parse_qucs_number(v, name, dat_path) for v in lines]
        arr = np.asarray(values)
        # Keep purely real columns (the frequency axis) real, so callers can
        # use them as an axis without tripping numpy's complex-cast warning.
        if np.iscomplexobj(arr) and not np.any(arr.imag):
            arr = arr.real
        out[name] = arr
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
            name = f"S[{i + 1},{j + 1}]"
            re_key, im_key = f"{name}.r", f"{name}.i"

            # qucsator-RF writes one complex column per S-parameter; older
            # builds split it into separate .r/.i real columns. Accept both.
            if name in data:
                values = np.asarray(data[name], dtype=np.complex128)
            elif re_key in data and im_key in data:
                # Validate each half against the sweep before combining, so a
                # short column is reported as such instead of silently
                # broadcasting or being blamed on its partner.
                for key in (re_key, im_key):
                    if data[key].size != freq_hz.size:
                        raise ValueError(
                            f"{key} in {dat_path} has {data[key].size} "
                            f"points but frequency has {freq_hz.size}"
                        )
                values = data[re_key] + 1j * data[im_key]
            else:
                raise ValueError(f"Missing {name} in {dat_path}. Found variables: {sorted(data)}")

            if values.size != freq_hz.size:
                raise ValueError(
                    f"{name} in {dat_path} has {values.size} points "
                    f"but frequency has {freq_hz.size}"
                )
            s[:, i, j] = values
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
