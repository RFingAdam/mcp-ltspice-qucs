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
from typing import Any, Literal

import numpy as np
import skrf as rf
from numpy.typing import NDArray

from rf_mcp_common.logging import get_logger
from rf_mcp_common.touchstone import network_to_touchstone

log = get_logger("mcp_ltspice.extract")

# Impedance / admittance vectors arrive from numpy arithmetic as
# `complexfloating[Any, Any]`, not the narrower `complex128`. Accept the
# wide form on input; the ABCD builders always emit complex128.
ComplexArray = NDArray[np.complexfloating[Any, Any]]

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


def _abcd_series_z(z: ComplexArray) -> NDArray[np.complex128]:
    """ABCD matrix for a series impedance Z. Returns shape (N, 2, 2)."""
    out = np.zeros((z.size, 2, 2), dtype=np.complex128)
    out[:, 0, 0] = 1.0
    out[:, 0, 1] = z
    out[:, 1, 0] = 0.0
    out[:, 1, 1] = 1.0
    return out


def _abcd_shunt_y(y: ComplexArray) -> NDArray[np.complex128]:
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
            z_trap: ComplexArray = s_axis * l_t + 1.0 / (s_axis * c_t)
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
            # Floor the denominator *before* dividing, mirroring the
            # shunt_lc_trap branch above. Clamping the quotient afterwards
            # (|z| > ceiling) cannot catch the anti-resonant bin: there
            # s²LC+1 → 0 with numerator → 0 too, so the quotient is NaN,
            # and `abs(NaN) > ceiling` is False — the clamp silently misses
            # and the NaN propagates into the S-matrix.
            den = s_axis**2 * l_t * c_t + 1.0
            den_floor = 1e-30
            with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                den = np.where(np.abs(den) < den_floor, den_floor + 0j, den)
                z_par = s_axis * l_t / den
            mat = _abcd_series_z(z_par)
        else:
            raise ValueError(f"Unknown element type: {kind}")
        abcd = _chain(abcd, mat)

    a = abcd[:, 0, 0]
    b = abcd[:, 0, 1]
    c = abcd[:, 1, 0]
    d = abcd[:, 1, 1]

    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        denom = a + b / z0 + c * z0 + d
        s11 = (a + b / z0 - c * z0 - d) / denom
        s21 = 2.0 / denom
        # S12 = 2·det(ABCD)/denom, and det ≡ 1 here: every element is built
        # by _abcd_series_z or _abcd_shunt_y, both of which have det 1, and
        # det is multiplicative over the cascade. Evaluating `a*d - b*c`
        # numerically instead overflows once the ladder gets long (a 9th-order
        # BSF does it), yielding inf → the isfinite guard below rewrote S12 to
        # 0 while S21 stayed finite — a silent reciprocity violation.
        s12 = s21
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
    seen = set()
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
    assume_reciprocal_symmetric: bool = True,
) -> rf.Network:
    """Extract 2-port S-parameters from an LTspice/ngspice ``.raw`` file.

    ``port_map`` maps port index (1, 2, ...) to the SPICE node name at
    that port. Convention written by :mod:`mcp_ltspice.asc_io`:

    - Port 1 is driven by ``V1 AC 1 0`` through series resistor ``Rs1 = z0``,
      landing on node ``port_map[1]``.
    - Port 2 is the network output node ``port_map[2]``, terminated in
      ``RL1 = z0`` to ground (no separate source).

    From a single AC sweep we recover column 1 of the scattering matrix
    exactly:

    - ``a₁ = (V₁_src) / (2 √Z₀) = 1 / (2√Z₀)`` (since ``V₁_src = AC 1``)
    - ``b₁ = (V_p1 − Z₀·I_Rs1) / (2 √Z₀)``  →  ``S11 = b₁ / a₁``
    - ``b₂ = V_p2 / √Z₀``  (port 2 terminated in Z₀, so ``a₂ = 0``)
      →  ``S21 = b₂ / a₁``

    To populate column 2 we'd need a second AC sweep with the source
    moved to port 2 (the runner does not orchestrate this today). When
    ``assume_reciprocal_symmetric=True`` (the default) we fill column 2
    by reciprocity (``S12 = S21``) and symmetry (``S22 = S11``). This is
    exact for the lumped passive ladder filters this package synthesises;
    for asymmetric or active networks, set ``assume_reciprocal_symmetric=False``
    and run two sweeps.
    """
    from spicelib import RawRead

    raw = RawRead(str(raw_path))
    freq_trace = raw.get_trace("frequency")
    if freq_trace is None:
        raise ValueError("No 'frequency' trace in raw file (expected AC analysis)")
    freq_hz = np.asarray(freq_trace.get_wave(), dtype=np.complex128).real

    nports = len(port_map)
    if nports != 2:
        raise NotImplementedError(
            f"extract_sparams_from_raw currently supports 2-port networks; got nports={nports}"
        )

    p1_node = port_map[1]
    p2_node = port_map[2]

    def _trace(name: str):
        """``get_trace`` raises rather than returning ``None`` for an unknown
        trace, so the old ``is None`` guards below were unreachable."""
        try:
            return raw.get_trace(name)
        except (IndexError, KeyError):
            return None

    v1_trace = _trace(f"V({p1_node})")
    v2_trace = _trace(f"V({p2_node})")
    i1_trace = _trace("I(Rs1)")
    if v1_trace is None or v2_trace is None:
        missing = [
            name
            for name, t in [(f"V({p1_node})", v1_trace), (f"V({p2_node})", v2_trace)]
            if t is None
        ]
        available = [t.name for t in getattr(raw, "_trace_info", [])]
        raise ValueError(f"Missing required traces in .raw file: {missing}. Available: {available}")

    v_p1 = np.asarray(v1_trace.get_wave(), dtype=np.complex128)
    v_p2 = np.asarray(v2_trace.get_wave(), dtype=np.complex128)

    # Port-1 current, by Ohm's law rather than from the I(Rs1) trace.
    #
    # This is exact under the port convention already assumed a few lines
    # below (V1 = AC 1 driving through Rs1 = z0 into p1): the current into
    # port 1 is (V_src - V_p1)/z0 with V_src = 1. Deriving it buys two
    # things over reading the trace:
    #
    # - ngspice does not record resistor currents at all, so the trace is
    #   simply absent there and extraction used to die with an IndexError.
    # - SPICE defines a two-terminal device's current as flowing from its
    #   first node to its second, and which pin LTspice emits first depends
    #   on the symbol's orientation in the schematic. LTspice netlists our
    #   generated schematic as `Rs1 p1 N001`, so its I(Rs1) runs *out* of
    #   port 1 — the opposite sign to the one this code assumed, which put
    #   S11 at 0 dB (fully reflective) for a well-matched filter.
    i_rs1 = (1.0 - v_p1) / z0

    if i1_trace is not None:
        # The trace is still useful as a consistency check: magnitudes must
        # agree even though the sign is orientation-dependent. A mismatch
        # means the schematic does not follow the documented port-1
        # convention, so every S-parameter below is suspect.
        measured = np.asarray(i1_trace.get_wave(), dtype=np.complex128)
        scale = float(np.max(np.abs(i_rs1))) or 1.0
        deviation = float(np.max(np.abs(np.abs(measured) - np.abs(i_rs1)))) / scale
        if deviation > 1e-3:
            log.warning(
                "I(Rs1) in %s disagrees with the current implied by V(%s) "
                "(max relative deviation %.3g). The schematic may not follow the "
                "expected port-1 convention (V1 = AC 1 through Rs1 = z0 into %s); "
                "S-parameters may be wrong.",
                raw_path,
                p1_node,
                deviation,
                p1_node,
            )

    sqrt_z0 = np.sqrt(z0)
    a1 = 1.0 / (2.0 * sqrt_z0)  # V1_src = AC 1, scalar (broadcast)
    b1 = (v_p1 - z0 * i_rs1) / (2.0 * sqrt_z0)
    b2 = v_p2 / sqrt_z0  # a2 = 0 (port 2 terminated in z0)

    s11 = b1 / a1
    s21 = b2 / a1

    s = np.zeros((freq_hz.size, 2, 2), dtype=np.complex128)
    s[:, 0, 0] = s11
    s[:, 1, 0] = s21

    if assume_reciprocal_symmetric:
        s[:, 0, 1] = s21  # reciprocity
        s[:, 1, 1] = s11  # symmetry
    # else: leave column 2 zero; caller is expected to merge with a port-2 sweep

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
