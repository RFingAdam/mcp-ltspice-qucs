"""S-parameter computation.

Two paths:

1. **Analytical** — given a list of LC ladder components, compute S₁₁ and
   S₂₁ from the cascaded ABCD matrices (lossless, ideal). No simulator
   required, used for fast design-space exploration and synthesis
   validation.

2. **Simulator-extracted** — parse a SPICE ``.raw`` AC-analysis output
   from LTspice / ngspice and compute S-parameters via the standard
   2-port voltage / current method.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import numpy as np
import skrf as rf
from numpy.typing import NDArray
from rf_mcp_common.touchstone import network_to_touchstone

ElementType = Literal["series_l", "shunt_c", "shunt_l", "series_c", "shunt_lc_trap"]


def _abcd_series_z(z: NDArray[np.complex128]) -> NDArray[np.complex128]:
    """ABCD matrix for a series impedance Z. Returns shape (N, 2, 2)."""
    out = np.zeros((z.size, 2, 2), dtype=np.complex128)
    out[:, 0, 0] = 1.0
    out[:, 0, 1] = z
    out[:, 1, 0] = 0.0
    out[:, 1, 1] = 1.0
    return out


def _abcd_shunt_y(y: NDArray[np.complex128]) -> NDArray[np.complex128]:
    """ABCD matrix for a shunt admittance Y. Returns shape (N, 2, 2)."""
    out = np.zeros((y.size, 2, 2), dtype=np.complex128)
    out[:, 0, 0] = 1.0
    out[:, 0, 1] = 0.0
    out[:, 1, 0] = y
    out[:, 1, 1] = 1.0
    return out


def _chain(a: NDArray[np.complex128], b: NDArray[np.complex128]) -> NDArray[np.complex128]:
    """Per-frequency 2×2 matrix product for ABCD chain."""
    return np.einsum("nij,njk->nik", a, b)


def ladder_sparams_from_components(
    elements: list[tuple[ElementType, dict[str, float]]],
    freq_hz: NDArray[np.float64],
    *,
    z0: float = 50.0,
) -> NDArray[np.complex128]:
    """Compute S-parameters for a lossless LC ladder.

    ``elements`` is an ordered source-to-load list of element tuples:

    - ``("series_l", {"L": 6.2e-9})``
    - ``("series_c", {"C": 2.2e-12})``
    - ``("shunt_c", {"C": 2.2e-12})``
    - ``("shunt_l", {"L": 4.7e-9})``
    - ``("shunt_lc_trap", {"L": 4.7e-9, "C": 1.8e-12})``  (series LC to GND)

    Returns S of shape (npoints, 2, 2).
    """
    s_axis = 1j * 2.0 * np.pi * freq_hz
    abcd = np.broadcast_to(np.eye(2, dtype=np.complex128), (s_axis.size, 2, 2)).copy()

    for kind, params in elements:
        if kind == "series_l":
            z = s_axis * params["L"]
            mat = _abcd_series_z(z)
        elif kind == "series_c":
            z = 1.0 / (s_axis * params["C"])
            mat = _abcd_series_z(z)
        elif kind == "shunt_c":
            y = s_axis * params["C"]
            mat = _abcd_shunt_y(y)
        elif kind == "shunt_l":
            y = 1.0 / (s_axis * params["L"])
            mat = _abcd_shunt_y(y)
        elif kind == "shunt_lc_trap":
            l_t = params["L"]
            c_t = params["C"]
            # Series LC to ground: Z = sL + 1/(sC); Y = 1/Z
            # At resonance Z → 0 and Y → ∞; clamp |Z| to a small floor so
            # the limit (perfect short to ground) evaluates as finite-precision
            # huge admittance instead of NaN.
            z_trap = s_axis * l_t + 1.0 / (s_axis * c_t)
            z_floor = 1e-30
            with np.errstate(divide="ignore", invalid="ignore"):
                z_trap = np.where(np.abs(z_trap) < z_floor, z_floor + 0j, z_trap)
                y = 1.0 / z_trap
            mat = _abcd_shunt_y(y)
        else:
            raise ValueError(f"Unknown element type: {kind}")
        abcd = _chain(abcd, mat)

    a = abcd[:, 0, 0]
    b = abcd[:, 0, 1]
    c = abcd[:, 1, 0]
    d = abcd[:, 1, 1]

    with np.errstate(divide="ignore", invalid="ignore"):
        denom = a + b / z0 + c * z0 + d
        s11 = (a + b / z0 - c * z0 - d) / denom
        s21 = 2.0 / denom
        s12 = 2.0 * (a * d - b * c) / denom
        s22 = (-a + b / z0 - c * z0 + d) / denom

    s = np.zeros((freq_hz.size, 2, 2), dtype=np.complex128)
    s[:, 0, 0] = np.where(np.isfinite(s11), s11, -1.0)
    s[:, 0, 1] = np.where(np.isfinite(s12), s12, 0.0)
    s[:, 1, 0] = np.where(np.isfinite(s21), s21, 0.0)
    s[:, 1, 1] = np.where(np.isfinite(s22), s22, -1.0)
    return s


def components_dict_to_elements(
    components: dict[str, float],
    *,
    topology: str = "series_first",
    transmission_zeros: bool = False,
) -> list[tuple[ElementType, dict[str, float]]]:
    """Convert the synthesis-style component dict into an ordered element
    list suitable for :func:`ladder_sparams_from_components`.

    For Butterworth/Chebyshev (``transmission_zeros=False``):
    components are L1, C2, L3, C4, ... (series_first) or C1, L2, ...
    (shunt_first). Indices encode position; we walk in numeric order.

    For elliptic (``transmission_zeros=True``):
    components are L1, L2+C2 (trap), L3, L4+C4 (trap), L5, ...
    Pairs (Lk, Ck) for even k form shunt LC traps; lone Lk are series.
    """
    # Sort by numeric refdes index
    def _idx(name: str) -> int:
        m = re.match(r"[LC](\d+)", name)
        if not m:
            raise ValueError(f"Bad refdes: {name}")
        return int(m.group(1))

    sorted_names = sorted(components.keys(), key=_idx)
    elements: list[tuple[ElementType, dict[str, float]]] = []

    if not transmission_zeros:
        # Walk and emit series_l / shunt_c per topology
        for name in sorted_names:
            kind_letter = name[0]
            value = components[name]
            if topology == "series_first":
                if kind_letter == "L":
                    elements.append(("series_l", {"L": value}))
                else:
                    elements.append(("shunt_c", {"C": value}))
            else:  # shunt_first
                if kind_letter == "L":
                    elements.append(("series_l", {"L": value}))
                else:
                    elements.append(("shunt_c", {"C": value}))
        return elements

    # Elliptic case: pair up traps
    seen: set[str] = set()
    for name in sorted_names:
        if name in seen:
            continue
        idx = _idx(name)
        l_key = f"L{idx}"
        c_key = f"C{idx}"
        if l_key in components and c_key in components:
            # Trap
            elements.append(
                ("shunt_lc_trap", {"L": components[l_key], "C": components[c_key]})
            )
            seen.add(l_key)
            seen.add(c_key)
        elif l_key in components:
            elements.append(("series_l", {"L": components[l_key]}))
            seen.add(l_key)
        else:
            elements.append(("shunt_c", {"C": components[c_key]}))
            seen.add(c_key)
    return elements


def write_sparams_touchstone(
    components: dict[str, float],
    freq_hz: NDArray[np.float64],
    out_path: str | Path,
    *,
    z0: float = 50.0,
    topology: str = "series_first",
    transmission_zeros: bool = False,
    name: str | None = None,
) -> Path:
    """Convenience: synthesize → S-params → write .s2p."""
    elements = components_dict_to_elements(
        components, topology=topology, transmission_zeros=transmission_zeros
    )
    s = ladder_sparams_from_components(elements, freq_hz, z0=z0)
    return network_to_touchstone(freq_hz, s, out_path, z0=z0, name=name)


# --------------------------------------------------------------------------
# Simulator output parsing
# --------------------------------------------------------------------------


def extract_sparams_from_raw(
    raw_path: str | Path,
    *,
    port_map: dict[int, str],
    z0: float = 50.0,
) -> rf.Network:
    """Extract 2-port S-parameters from an LTspice/ngspice ``.raw`` file.

    ``port_map`` maps port index (1, 2, ...) to the SPICE node name driven
    by the corresponding source. Convention: each port is driven by an AC
    voltage source with series Z0; we reconstruct S-parameters from the
    voltage at each port node and the current into the network.

    The convention assumed for the schematic:
    - Port k is driven by an AC source ``Vk`` with ``AC 1`` and series
      resistor ``Rsk = z0`` between ``Vk`` and the port node.
    - The waveform names in the .raw are ``V(<node>)`` for node voltages
      and ``I(Rsk)`` for currents through the source resistor.
    """
    from spicelib import RawRead

    raw = RawRead(str(raw_path))
    freq_trace = raw.get_trace("frequency")
    if freq_trace is None:
        raise ValueError("No 'frequency' trace in raw file (expected AC analysis)")
    freq_hz = np.asarray(freq_trace.get_wave(), dtype=np.complex128).real

    nports = len(port_map)
    s = np.zeros((freq_hz.size, nports, nports), dtype=np.complex128)

    for k, node_k in port_map.items():
        v_trace = raw.get_trace(f"V({node_k})")
        i_trace = raw.get_trace(f"I(Rs{k})")
        if v_trace is None or i_trace is None:
            raise ValueError(
                f"Missing trace for port {k}: need V({node_k}) and I(Rs{k})"
            )
        v_k = np.asarray(v_trace.get_wave(), dtype=np.complex128)
        i_k = np.asarray(i_trace.get_wave(), dtype=np.complex128)

        # Incident wave at port k: a_k = (V_k + Z0*I_k) / (2 sqrt(Z0))
        # Reflected wave:          b_k = (V_k - Z0*I_k) / (2 sqrt(Z0))
        a_k = (v_k + z0 * i_k) / (2.0 * np.sqrt(z0))
        b_k = (v_k - z0 * i_k) / (2.0 * np.sqrt(z0))
        s[:, k - 1, k - 1] = b_k / a_k  # NB: only valid when other ports are matched

    # For full S-matrix we need a separate run per excitation. The AC
    # method here yields the diagonal under the assumption that all
    # other source AC magnitudes are zero. The runner orchestrates one
    # AC sim per port and stitches them.
    return rf.Network(
        frequency=rf.Frequency.from_f(freq_hz, unit="Hz"),
        s=s,
        z0=z0,
        name=Path(raw_path).stem,
    )


_NODE_RE = re.compile(r"V\(([^)]+)\)", re.IGNORECASE)


def list_raw_nodes(raw_path: str | Path) -> list[str]:
    """Return the list of node names (V(...)) present in the raw file."""
    from spicelib import RawRead

    raw = RawRead(str(raw_path))
    nodes = []
    for trace_name in raw.get_trace_names():
        m = _NODE_RE.fullmatch(trace_name)
        if m:
            nodes.append(m.group(1))
    return nodes
