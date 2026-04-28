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

ElementType = Literal[
    "series_l",
    "shunt_c",
    "shunt_l",
    "series_c",
    "shunt_lc_trap",  # series-LC to GND (BSF shunt; elliptic LPF trap)
    "series_lc_series",  # series-LC in main path (BPF series-section)
    "shunt_lc_parallel",  # parallel-LC to GND (BPF shunt-section)
    "series_lc_parallel",  # parallel-LC in main path (BSF series-section)
]


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
    - ``("shunt_lc_trap", {"L": ..., "C": ...})`` — series-LC to GND (BSF shunt; elliptic LPF trap). ``Y = sC / (s²LC + 1)``; admittance peaks at ω₀.
    - ``("series_lc_series", {"L": ..., "C": ...})`` — series-LC in main path (BPF series-section). ``Z = sL + 1/(sC)``; impedance dips at ω₀.
    - ``("shunt_lc_parallel", {"L": ..., "C": ...})`` — parallel-LC to GND (BPF shunt-section). ``Y = sC + 1/(sL)``; admittance dips at ω₀, blocking signal flow into the shunt branch in-band so it passes to the next series element.
    - ``("series_lc_parallel", {"L": ..., "C": ...})`` — parallel-LC in main path (BSF series-section). ``Z = sL / (s²LC + 1)``; impedance peaks at ω₀.

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
        elif kind == "series_lc_series":
            # Series LC in main signal path: Z = sL + 1/(sC). Dips to 0 at ω₀.
            l_t = params["L"]
            c_t = params["C"]
            z = s_axis * l_t + 1.0 / (s_axis * c_t)
            mat = _abcd_series_z(z)
        elif kind == "shunt_lc_parallel":
            # Parallel LC to ground: Y = sC + 1/(sL). Dips to 0 at ω₀,
            # peaks toward inf at DC and ∞. The branch acts as a shunt
            # short to ground at low and high frequencies (blocking) and
            # opens up in-band, letting signal pass through the main path.
            l_t = params["L"]
            c_t = params["C"]
            y = s_axis * c_t + 1.0 / (s_axis * l_t)
            mat = _abcd_shunt_y(y)
        elif kind == "series_lc_parallel":
            # Parallel LC in main signal path: Z = sL/(s²LC+1). Peaks to
            # ∞ at ω₀ (anti-resonant — blocks in-band), goes to ~sL at DC
            # and ~1/(sC) at high frequency.
            l_t = params["L"]
            c_t = params["C"]
            z_par = s_axis * l_t / (s_axis**2 * l_t * c_t + 1.0)
            # Clamp at resonance to avoid Inf
            z_ceiling = 1e30
            z_par = np.where(np.abs(z_par) > z_ceiling, z_ceiling + 0j, z_par)
            mat = _abcd_series_z(z_par)
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


def _idx(name: str) -> int:
    """Numeric index from a refdes like ``L3`` or ``C12`` or ``C2_s``.

    BPF / BSF components carry an optional ``"_s"`` suffix on the cap
    that pairs with an inductor to form a series-LC resonator. The
    suffix is stripped for indexing so ``L2`` and ``C2_s`` share index 2.
    """
    m = re.match(r"[LC](\d+)(?:_s)?$", name)
    if not m:
        raise ValueError(f"Bad refdes: {name}")
    return int(m.group(1))


def infer_transmission_zeros(components: dict[str, float]) -> bool:
    """Detect whether a components dict represents an elliptic ladder.

    An elliptic LC ladder has L+C pairs at even indices (the shunt-LC
    traps). Butterworth / Chebyshev ladders have lone Ls and lone Cs
    alternating, with no even-indexed L+C pair.

    Returns ``True`` if any even-indexed ``Lk + Ck`` pair coexists.
    """
    for name in components:
        idx = _idx(name)
        if idx % 2 != 0:
            continue
        l_key = f"L{idx}"
        c_key = f"C{idx}"
        if l_key in components and c_key in components:
            return True
    return False


def components_dict_to_elements(
    components: dict[str, float],
    *,
    topology: str = "series_first",
    transmission_zeros: bool | None = None,
    kind: str = "lowpass",
) -> list[tuple[ElementType, dict[str, float]]]:
    """Convert the synthesis-style component dict into an ordered element
    list suitable for :func:`ladder_sparams_from_components`.

    Topology cases:

    - **Butterworth / Chebyshev** — components are ``L1, C2, L3, C4, ...``
      (``series_first``) or ``C1, L2, C3, L4, ...`` (``shunt_first``).
      Indices encode position; we walk in numeric order.

    - **Elliptic** — components are ``L1, L2+C2 (trap), L3, L4+C4 (trap), L5, ...``.
      ``Lk + Ck`` pairs at even ``k`` form shunt LC traps; lone ``Lk`` are series.

    The ``transmission_zeros`` flag selects which interpretation to apply:

    - ``None`` (default) — auto-infer from the components dict using
      :func:`infer_transmission_zeros`. **This is the recommended default.**
    - ``True`` — force elliptic (trap) interpretation
    - ``False`` — force Butterworth / Chebyshev interpretation

    If an explicit flag disagrees with what auto-inference would have
    chosen, a :class:`RuntimeWarning` is emitted recommending the user
    re-check their topology choice (typically, forgetting ``True`` for
    an elliptic ladder silently produces wrong S-parameters).
    """
    inferred = infer_transmission_zeros(components)
    if transmission_zeros is None:
        transmission_zeros = inferred
    elif transmission_zeros != inferred:
        import warnings

        if inferred and not transmission_zeros:
            warnings.warn(
                "components_dict_to_elements: explicit transmission_zeros=False, "
                "but the components dict has even-indexed L+C pairs (elliptic "
                "topology). The forced Butterworth/Chebyshev interpretation will "
                "produce wrong S-parameters. Pass transmission_zeros=True or "
                "leave it unset to auto-infer.",
                RuntimeWarning,
                stacklevel=2,
            )
        elif transmission_zeros and not inferred:
            warnings.warn(
                "components_dict_to_elements: explicit transmission_zeros=True, "
                "but the components dict has no even-indexed L+C pairs. There "
                "are no traps to pair up; the elliptic-mode walk will produce "
                "the same elements as the Butterworth/Chebyshev interpretation.",
                RuntimeWarning,
                stacklevel=2,
            )

    sorted_names = sorted(components.keys(), key=_idx)
    elements: list[tuple[ElementType, dict[str, float]]] = []

    # Bandpass: each LPF reactive maps to an LC pair.
    # series-first ⇒ odd-k = series-LC-series (BPF series section),
    #                 even-k = shunt-LC-parallel (BPF shunt section).
    if kind == "bandpass":
        seen: set[str] = set()
        sorted_indices = sorted({_idx(n) for n in components})
        for k in sorted_indices:
            l_key = f"L{k}"
            c_s_key = f"C{k}_s"
            c_key = f"C{k}"
            is_odd_k = k % 2 == 1
            in_main_path = (is_odd_k and topology == "series_first") or (
                not is_odd_k and topology == "shunt_first"
            )
            if l_key in components and c_s_key in components and in_main_path:
                # series-LC pair in main path — BPF series section
                elements.append(
                    ("series_lc_series", {"L": components[l_key], "C": components[c_s_key]})
                )
                seen.update({l_key, c_s_key})
            elif l_key in components and c_key in components and not in_main_path:
                # parallel-LC pair to ground — BPF shunt section
                elements.append(
                    ("shunt_lc_parallel", {"L": components[l_key], "C": components[c_key]})
                )
                seen.update({l_key, c_key})
            else:
                raise ValueError(
                    f"BPF kind: cannot pair components at index {k}; got "
                    f"L_in_dict={l_key in components}, "
                    f"C_in_dict={c_key in components}, "
                    f"C_s_in_dict={c_s_key in components}, "
                    f"in_main_path={in_main_path}"
                )
        return elements

    # Bandstop: same component-pair shape as BPF but resonator types flip.
    # series-first ⇒ odd-k = series-LC-parallel (anti-resonant in main path),
    #                 even-k = shunt-LC-trap (series LC to ground).
    if kind == "bandstop":
        sorted_indices = sorted({_idx(n) for n in components})
        for k in sorted_indices:
            l_key = f"L{k}"
            c_s_key = f"C{k}_s"
            c_key = f"C{k}"
            is_odd_k = k % 2 == 1
            in_main_path = (is_odd_k and topology == "series_first") or (
                not is_odd_k and topology == "shunt_first"
            )
            if l_key in components and c_s_key in components and in_main_path:
                # parallel-LC in main path — BSF series section
                elements.append(
                    ("series_lc_parallel", {"L": components[l_key], "C": components[c_s_key]})
                )
            elif l_key in components and c_key in components and not in_main_path:
                # series-LC to ground — BSF shunt section (== existing trap kind)
                elements.append(("shunt_lc_trap", {"L": components[l_key], "C": components[c_key]}))
            else:
                raise ValueError(
                    f"BSF kind: cannot pair components at index {k}; got "
                    f"L_in_dict={l_key in components}, "
                    f"C_in_dict={c_key in components}, "
                    f"C_s_in_dict={c_s_key in components}, "
                    f"in_main_path={in_main_path}"
                )
        return elements

    # Highpass: odd-k = series-C, even-k = shunt-L (series_first); reversed for shunt_first.
    if kind == "highpass":
        for name in sorted_names:
            idx = _idx(name)
            value = components[name]
            kind_letter = name[0]
            is_odd_k = idx % 2 == 1
            if topology == "series_first":
                if is_odd_k:
                    # series position — must be a C in HPF
                    if kind_letter != "C":
                        raise ValueError(f"HPF series_first expects C at odd index, got {name}")
                    elements.append(("series_c", {"C": value}))
                else:
                    if kind_letter != "L":
                        raise ValueError(f"HPF series_first expects L at even index, got {name}")
                    elements.append(("shunt_l", {"L": value}))
            else:  # shunt_first
                if is_odd_k:
                    if kind_letter != "L":
                        raise ValueError(f"HPF shunt_first expects L at odd index, got {name}")
                    elements.append(("shunt_l", {"L": value}))
                else:
                    if kind_letter != "C":
                        raise ValueError(f"HPF shunt_first expects C at even index, got {name}")
                    elements.append(("series_c", {"C": value}))
        return elements

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
            elements.append(("shunt_lc_trap", {"L": components[l_key], "C": components[c_key]}))
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
    transmission_zeros: bool | None = None,
    name: str | None = None,
) -> Path:
    """Convenience: synthesize → S-params → write .s2p.

    ``transmission_zeros`` defaults to ``None`` (auto-infer); pass an
    explicit ``bool`` to override. See :func:`components_dict_to_elements`.
    """
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
            raise ValueError(f"Missing trace for port {k}: need V({node_k}) and I(Rs{k})")
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
