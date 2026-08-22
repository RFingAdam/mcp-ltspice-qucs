"""S-parameter computation.

Two paths:

1. **Analytical**: given a list of LC ladder components, compute S₁₁ and
   S₂₁ from the cascaded ABCD matrices (lossless, ideal). No simulator
   required, used for fast design-space exploration and synthesis
   validation.

2. **Simulator-extracted**: parse a SPICE ``.raw`` AC-analysis output
   from LTspice / ngspice and compute S-parameters via the standard
   2-port voltage / current method.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

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
    "shunt_composite_trap",  # series-LC + parallel-LC in series, to GND (elliptic BPF/BSF trap)
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
    return cast(NDArray[np.complex128], np.einsum("nij,njk->nik", a, b))


def _inductor_impedance(
    s_axis: NDArray[np.complex128], params: dict[str, float], role: str = "L"
) -> NDArray[np.complex128]:
    """First-order inductor model: ``(Rs + sL) || Cp`` when metadata exists."""
    z_series = params.get(f"{role}_Rs", 0.0) + s_axis * params[role]
    cp = params.get(f"{role}_Cp", 0.0)
    if cp <= 0:
        return z_series
    with np.errstate(divide="ignore", invalid="ignore"):
        return 1.0 / (1.0 / z_series + s_axis * cp)


def _capacitor_impedance(
    s_axis: NDArray[np.complex128], params: dict[str, float], role: str = "C"
) -> NDArray[np.complex128]:
    """First-order capacitor model: ``Rs + sLs + 1/(sC)``."""
    return (
        params.get(f"{role}_Rs", 0.0)
        + s_axis * params.get(f"{role}_Ls", 0.0)
        + 1.0 / (s_axis * params[role])
    )


def ladder_sparams_from_components(
    elements: list[tuple[ElementType, dict[str, float]]],
    freq_hz: NDArray[np.float64],
    *,
    z0: float = 50.0,
) -> NDArray[np.complex128]:
    """Compute S-parameters for an ideal or first-order-realized LC ladder.

    ``elements`` is an ordered source-to-load list of element tuples:

    - ``("series_l", {"L": 6.2e-9})``
    - ``("series_c", {"C": 2.2e-12})``
    - ``("shunt_c", {"C": 2.2e-12})``
    - ``("shunt_l", {"L": 4.7e-9})``
    - ``("shunt_lc_trap", {"L": ..., "C": ...})``: series-LC to GND (BSF shunt; elliptic LPF trap). ``Y = sC / (s²LC + 1)``; admittance peaks at ω₀.
    - ``("series_lc_series", {"L": ..., "C": ...})``: series-LC in main path (BPF series-section). ``Z = sL + 1/(sC)``; impedance dips at ω₀.
    - ``("shunt_lc_parallel", {"L": ..., "C": ...})``: parallel-LC to GND (BPF shunt-section). ``Y = sC + 1/(sL)``; admittance dips at ω₀, blocking signal flow into the shunt branch in-band so it passes to the next series element.
    - ``("series_lc_parallel", {"L": ..., "C": ...})``: parallel-LC in main path (BSF series-section). ``Z = sL / (s²LC + 1)``; impedance peaks at ω₀.
    - ``("shunt_composite_trap", {"L_s": ..., "C_s": ..., "L_p": ..., "C_p": ...})``: series-LC (L_s, C_s) in series with a parallel-LC tank (L_p ∥ C_p), the whole branch to GND. The image of an elliptic LPF trap under the BPF/BSF transform. ``Z = sL_s + 1/(sC_s) + sL_p/(s²L_pC_p + 1)``; the branch shorts at the two mapped transmission zeros. The roots of ``u²·L_sC_sL_pC_p − u·(L_sC_s + L_pC_p + L_pC_s) + 1 = 0`` in ``u = ω²``.

    Returns S of shape (npoints, 2, 2).
    """
    s_axis = np.asarray(1j * 2.0 * np.pi * freq_hz, dtype=np.complex128)
    abcd = np.broadcast_to(np.eye(2, dtype=np.complex128), (s_axis.size, 2, 2)).copy()

    for kind, params in elements:
        if kind == "series_l":
            z = _inductor_impedance(s_axis, params)
            mat = _abcd_series_z(z)
        elif kind == "series_c":
            z = _capacitor_impedance(s_axis, params)
            mat = _abcd_series_z(z)
        elif kind == "shunt_c":
            y = 1.0 / _capacitor_impedance(s_axis, params)
            mat = _abcd_shunt_y(y)
        elif kind == "shunt_l":
            y = 1.0 / _inductor_impedance(s_axis, params)
            mat = _abcd_shunt_y(y)
        elif kind == "shunt_lc_trap":
            # Series LC to ground: Z = sL + 1/(sC); Y = 1/Z
            # At resonance Z → 0 and Y → ∞; clamp |Z| to a small floor so
            # the limit (perfect short to ground) evaluates as finite-precision
            # huge admittance instead of NaN.
            z_trap = _inductor_impedance(s_axis, params) + _capacitor_impedance(s_axis, params)
            z_floor = 1e-30
            with np.errstate(divide="ignore", invalid="ignore"):
                z_trap = np.where(np.abs(z_trap) < z_floor, z_floor + 0j, z_trap)
                y = 1.0 / z_trap
            mat = _abcd_shunt_y(y)
        elif kind == "series_lc_series":
            # Series LC in main signal path: Z = sL + 1/(sC). Dips to 0 at ω₀.
            z = _inductor_impedance(s_axis, params) + _capacitor_impedance(s_axis, params)
            mat = _abcd_series_z(z)
        elif kind == "shunt_lc_parallel":
            # Parallel LC to ground: Y = sC + 1/(sL). Dips to 0 at ω₀,
            # peaks toward inf at DC and ∞. The branch acts as a shunt
            # short to ground at low and high frequencies (blocking) and
            # opens up in-band, letting signal pass through the main path.
            y = 1.0 / _capacitor_impedance(s_axis, params) + 1.0 / _inductor_impedance(
                s_axis, params
            )
            mat = _abcd_shunt_y(y)
        elif kind == "series_lc_parallel":
            # Parallel LC in main signal path: Z = sL/(s²LC+1). Peaks to
            # ∞ at ω₀ (anti-resonant: blocks in-band), goes to ~sL at DC
            # and ~1/(sC) at high frequency.
            # Floor the denominator *before* dividing, mirroring the
            # shunt_lc_trap branch above. Clamping the quotient afterwards
            # (|z| > ceiling) cannot catch the anti-resonant bin: there
            # s²LC+1 → 0 with numerator → 0 too, so the quotient is NaN,
            # and `abs(NaN) > ceiling` is False. The clamp silently misses
            # and the NaN propagates into the S-matrix.
            y_parallel = 1.0 / _inductor_impedance(s_axis, params) + 1.0 / _capacitor_impedance(
                s_axis, params
            )
            den_floor = 1e-30
            with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                y_parallel = np.where(np.abs(y_parallel) < den_floor, den_floor + 0j, y_parallel)
                z_par = 1.0 / y_parallel
            mat = _abcd_series_z(z_par)
        elif kind == "shunt_composite_trap":
            # Series-LC in series with a parallel-LC tank, to ground. Two
            # floors: the tank denominator (its anti-resonance is a pole of
            # the branch impedance: benign, Y → 0) and |Z| itself (the two
            # branch resonances are shorts to ground, Y → ∞), mirroring the
            # shunt_lc_trap / series_lc_parallel handling above.
            floor = 1e-30
            with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                y_parallel = 1.0 / _inductor_impedance(
                    s_axis, params, "L_p"
                ) + 1.0 / _capacitor_impedance(s_axis, params, "C_p")
                y_parallel = np.where(np.abs(y_parallel) < floor, floor + 0j, y_parallel)
                z_branch = (
                    _inductor_impedance(s_axis, params, "L_s")
                    + _capacitor_impedance(s_axis, params, "C_s")
                    + 1.0 / y_parallel
                )
                z_branch = np.where(np.abs(z_branch) < floor, floor + 0j, z_branch)
                y = 1.0 / z_branch
            mat = _abcd_shunt_y(y)
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
        # 0 while S21 stayed finite. A silent reciprocity violation.
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

    - **Butterworth / Chebyshev**: components are ``L1, C2, L3, C4, ...``
      (``series_first``) or ``C1, L2, C3, L4, ...`` (``shunt_first``).
      Indices encode position; we walk in numeric order.

    - **Elliptic**: components are ``L1, L2+C2 (trap), L3, L4+C4 (trap), L5, ...``.
      ``Lk + Ck`` pairs at even ``k`` form shunt LC traps; lone ``Lk`` are series.
      Under ``kind="bandpass"`` / ``"bandstop"`` each trap instead appears as
      the four-key group ``{Lk_s, Ck_s, Lk, Ck}``. A shunt composite branch
      (series-LC + parallel tank in series, to ground).

    The ``transmission_zeros`` flag selects which interpretation to apply:

    - ``None`` (default): auto-infer from the components dict using
      :func:`infer_transmission_zeros`. **This is the recommended default.**
    - ``True``: force elliptic (trap) interpretation
    - ``False``: force Butterworth / Chebyshev interpretation

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

    # Bandpass: each LPF reactive maps to an LC pair: except an elliptic
    # LPF trap, whose L and C transform separately into a FOUR-element
    # composite shunt branch {Lk_s, Ck_s} (series pair) + {Lk, Ck} (tank).
    # series-first ⇒ odd-k = series-LC-series (BPF series section),
    #                 even-k = shunt-LC-parallel (BPF shunt section).
    if kind == "bandpass":
        seen: set[str] = set()
        sorted_indices = sorted({_idx(n) for n in components})
        for k in sorted_indices:
            l_key = f"L{k}"
            l_s_key = f"L{k}_s"
            c_s_key = f"C{k}_s"
            c_key = f"C{k}"
            is_odd_k = k % 2 == 1
            in_main_path = (is_odd_k and topology == "series_first") or (
                not is_odd_k and topology == "shunt_first"
            )
            if l_s_key in components:
                if in_main_path or not all(x in components for x in (c_s_key, l_key, c_key)):
                    raise ValueError(
                        f"BPF kind: {l_s_key} marks an elliptic composite trap, which "
                        f"requires all of {{L{k}_s, C{k}_s, L{k}, C{k}}} at a shunt position"
                    )
                elements.append(
                    (
                        "shunt_composite_trap",
                        {
                            "L_s": components[l_s_key],
                            "C_s": components[c_s_key],
                            "L_p": components[l_key],
                            "C_p": components[c_key],
                        },
                    )
                )
                seen.update({l_s_key, c_s_key, l_key, c_key})
            elif l_key in components and c_s_key in components and in_main_path:
                # series-LC pair in main path: BPF series section
                elements.append(
                    ("series_lc_series", {"L": components[l_key], "C": components[c_s_key]})
                )
                seen.update({l_key, c_s_key})
            elif l_key in components and c_key in components and not in_main_path:
                # parallel-LC pair to ground: BPF shunt section
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

    # Bandstop: same component-pair shape as BPF but resonator types flip;
    # the elliptic composite trap keeps the same four-key shape.
    # series-first ⇒ odd-k = series-LC-parallel (anti-resonant in main path),
    #                 even-k = shunt-LC-trap (series LC to ground).
    if kind == "bandstop":
        sorted_indices = sorted({_idx(n) for n in components})
        for k in sorted_indices:
            l_key = f"L{k}"
            l_s_key = f"L{k}_s"
            c_s_key = f"C{k}_s"
            c_key = f"C{k}"
            is_odd_k = k % 2 == 1
            in_main_path = (is_odd_k and topology == "series_first") or (
                not is_odd_k and topology == "shunt_first"
            )
            if l_s_key in components:
                if in_main_path or not all(x in components for x in (c_s_key, l_key, c_key)):
                    raise ValueError(
                        f"BSF kind: {l_s_key} marks an elliptic composite trap, which "
                        f"requires all of {{L{k}_s, C{k}_s, L{k}, C{k}}} at a shunt position"
                    )
                elements.append(
                    (
                        "shunt_composite_trap",
                        {
                            "L_s": components[l_s_key],
                            "C_s": components[c_s_key],
                            "L_p": components[l_key],
                            "C_p": components[c_key],
                        },
                    )
                )
            elif l_key in components and c_s_key in components and in_main_path:
                # parallel-LC in main path: BSF series section
                elements.append(
                    ("series_lc_parallel", {"L": components[l_key], "C": components[c_s_key]})
                )
            elif l_key in components and c_key in components and not in_main_path:
                # series-LC to ground: BSF shunt section (== existing trap kind)
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

    # Highpass. All-pole: odd-k = series-C, even-k = shunt-L (series_first),
    # reversed for shunt_first. Elliptic: the LPF→HPF transform leaves each
    # shunt series-LC trap a shunt series-LC trap (see synthesize_lc_hpf), so
    # an even-index L+C pair is a trap and the series positions carry C.
    if kind == "highpass":
        has_traps = any(
            f"L{_idx(n)}" in components and f"C{_idx(n)}" in components for n in components
        )
        seen_hp: set[str] = set()
        for name in sorted_names:
            if name in seen_hp:
                continue
            idx = _idx(name)
            l_key, c_key = f"L{idx}", f"C{idx}"
            is_odd_k = idx % 2 == 1
            series_position = (is_odd_k and topology == "series_first") or (
                not is_odd_k and topology == "shunt_first"
            )
            if has_traps and l_key in components and c_key in components:
                # Elliptic shunt trap: series LC to ground, notch at 1/2π√(LC).
                elements.append(("shunt_lc_trap", {"L": components[l_key], "C": components[c_key]}))
                seen_hp.update({l_key, c_key})
            elif series_position:
                if name[0] != "C":
                    raise ValueError(f"HPF expects C in a series position, got {name}")
                elements.append(("series_c", {"C": components[name]}))
                seen_hp.add(name)
            else:
                if name[0] != "L":
                    raise ValueError(f"HPF expects L in a shunt position, got {name}")
                elements.append(("shunt_l", {"L": components[name]}))
                seen_hp.add(name)
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


def associate_element_refdes(
    elements: list[tuple[ElementType, dict[str, float]]],
    components: dict[str, float],
) -> list[dict[str, str]]:
    """Associate each element's L/C roles with the source component refdes."""
    used: set[str] = set()
    associations: list[dict[str, str]] = []
    roles = ("L_s", "C_s", "L_p", "C_p", "L", "C")
    for _element_kind, original in elements:
        association: dict[str, str] = {}
        for role in roles:
            if role not in original:
                continue
            prefix = role[0]
            wants_suffix = role.endswith("_s")
            candidates = [
                refdes
                for refdes, value in components.items()
                if refdes.startswith(prefix)
                and refdes not in used
                and np.isclose(value, original[role], rtol=1e-12, atol=0.0)
            ]
            preferred = [refdes for refdes in candidates if refdes.endswith("_s") == wants_suffix]
            refdes = sorted(preferred or candidates)[0] if (preferred or candidates) else None
            if refdes is None:
                raise ValueError(
                    f"cannot associate element role {role!r}={original[role]:.12g} "
                    "with a component refdes"
                )
            association[role] = refdes
            used.add(refdes)
        associations.append(association)

    if used != set(components):
        unused = sorted(set(components) - used)
        raise ValueError(f"component mapping contains unassociated refdes: {unused}")
    return associations


def attach_component_parasitics(
    elements: list[tuple[ElementType, dict[str, float]]],
    components: dict[str, float],
    substitution: dict[str, dict[str, Any]],
) -> list[tuple[ElementType, dict[str, float]]]:
    """Attach per-refdes first-order parasitics to reconstructed elements.

    The returned tuples retain the existing public element representation.
    Extra ``<role>_Rs``, ``<role>_Cp``, and ``<role>_Ls`` values are consumed
    by :func:`ladder_sparams_from_components`. Every component must have a
    selected model record; partial realization is rejected.
    """
    missing = sorted(set(components) - set(substitution))
    if missing:
        raise ValueError(f"component substitution is missing refdes: {missing}")

    associations = associate_element_refdes(elements, components)
    realized: list[tuple[ElementType, dict[str, float]]] = []
    for (element_kind, original), association in zip(elements, associations, strict=True):
        params = dict(original)
        for role, refdes in association.items():
            selected = substitution[refdes]
            if "model" not in selected:
                raise ValueError(f"{refdes}: substitution lacks a ComponentModel record")
            params[f"{role}_Rs"] = float(selected.get("Rs", 0.0))
            if role.startswith("L"):
                params[f"{role}_Cp"] = float(selected.get("Cp", 0.0))
            else:
                params[f"{role}_Ls"] = float(selected.get("Ls", 0.0))
        realized.append((element_kind, params))
    return realized


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


#: Dialects spicelib can parse, tried in turn when auto-detection fails.
RAW_DIALECTS = ("ngspice", "ltspice", "xyce", "qspice")


def _open_raw(raw_path: str | Path, dialect: str | None = None) -> Any:
    """Open a ``.raw`` file, falling back to explicit dialects.

    spicelib infers the dialect from the header, which is not stable across
    simulator versions: ngspice 44.2 writes ``Command: ngspice-44.2, Build
    ...`` and is recognised, while other builds write headers that defeat
    detection and raise "file dialect is not specified and could not be auto
    detected". Since the file on disk is perfectly readable once the dialect
    is stated, try each one rather than failing the extraction.
    """
    from spicelib import RawRead
    from spicelib.raw.raw_classes import SpiceReadException

    if dialect is not None:
        return RawRead(str(raw_path), dialect=dialect)

    try:
        return RawRead(str(raw_path))
    except SpiceReadException as auto_failed:
        for candidate in RAW_DIALECTS:
            try:
                raw = RawRead(str(raw_path), dialect=candidate)
            except SpiceReadException:
                continue
            log.info(
                "Could not auto-detect the dialect of %s; parsed it as %r.",
                raw_path,
                candidate,
            )
            return raw
        raise SpiceReadException(
            f"Could not read {raw_path} with auto-detection or any known dialect "
            f"({', '.join(RAW_DIALECTS)}). Original error: {auto_failed}"
        ) from auto_failed


@dataclass(frozen=True)
class ExcitationResult:
    """One measured column of a two-port scattering matrix."""

    freq_hz: NDArray[np.float64]
    driven_port: int
    column: NDArray[np.complex128]
    current_magnitude_residual: float | None


def _extract_excitation_column(
    raw_path: str | Path,
    *,
    port_map: dict[int, str],
    driven_port: Literal[1, 2],
    z0: float = 50.0,
    source_resistor: str,
    dialect: str | None = None,
) -> ExcitationResult:
    """Recover one S-matrix column from one matched-port AC excitation."""
    if set(port_map) != {1, 2}:
        raise ValueError(f"port_map must contain exactly ports 1 and 2; got {sorted(port_map)}")
    if z0 <= 0:
        raise ValueError(f"z0 must be > 0; got {z0}")

    raw = _open_raw(raw_path, dialect)
    freq_trace = raw.get_trace("frequency")
    if freq_trace is None:
        raise ValueError("No 'frequency' trace in raw file (expected AC analysis)")
    freq_hz = np.asarray(freq_trace.get_wave(), dtype=np.complex128).real.astype(np.float64)
    if freq_hz.size == 0 or not np.all(np.isfinite(freq_hz)) or np.any(np.diff(freq_hz) <= 0):
        raise ValueError(f"{raw_path} has an empty, non-finite, or non-increasing frequency grid")

    def _trace(name: str) -> Any:
        try:
            return raw.get_trace(name)
        except (IndexError, KeyError):
            return None

    traces = {port: _trace(f"V({node})") for port, node in port_map.items()}
    missing = [f"V({port_map[port]})" for port, trace in traces.items() if trace is None]
    if missing:
        available = [t.name for t in getattr(raw, "_trace_info", [])]
        raise ValueError(
            f"Missing required traces in {raw_path}: {missing}. Available: {available}"
        )

    voltages = {
        port: np.asarray(trace.get_wave(), dtype=np.complex128) for port, trace in traces.items()
    }
    if any(wave.shape != freq_hz.shape for wave in voltages.values()):
        raise ValueError(f"{raw_path} voltage traces do not match its frequency grid")

    driven_voltage = voltages[driven_port]
    other_port = 2 if driven_port == 1 else 1
    # Fixture contract: a 1 V AC source drives the selected port through Z0;
    # the other port is terminated directly in Z0. Thus a_i=1/(2√Z0).
    source_current = (1.0 - driven_voltage) / z0
    sqrt_z0 = np.sqrt(z0)
    incident = 1.0 / (2.0 * sqrt_z0)
    reflected = (driven_voltage - z0 * source_current) / (2.0 * sqrt_z0)
    transmitted = voltages[other_port] / sqrt_z0

    column = np.empty((freq_hz.size, 2), dtype=np.complex128)
    column[:, driven_port - 1] = reflected / incident
    column[:, other_port - 1] = transmitted / incident

    current_trace = _trace(f"I({source_resistor})")
    residual: float | None = None
    if current_trace is not None:
        measured = np.asarray(current_trace.get_wave(), dtype=np.complex128)
        if measured.shape != freq_hz.shape:
            raise ValueError(f"I({source_resistor}) does not match the frequency grid")
        scale = float(np.max(np.abs(source_current))) or 1.0
        residual = float(np.max(np.abs(np.abs(measured) - np.abs(source_current)))) / scale
        if residual > 1e-3:
            raise ValueError(
                f"{raw_path} violates the declared port-{driven_port} fixture: "
                f"|I({source_resistor})| differs from (1-V({port_map[driven_port]}))/{z0:g} "
                f"by {residual:.3g} relative"
            )

    return ExcitationResult(
        freq_hz=freq_hz,
        driven_port=driven_port,
        column=column,
        current_magnitude_residual=residual,
    )


def extract_two_sweep_sparams(
    port1_raw_path: str | Path,
    port2_raw_path: str | Path,
    *,
    port_map: dict[int, str],
    z0: float = 50.0,
    port1_source_resistor: str = "Rs1",
    port2_source_resistor: str = "RL1",
    dialect: str | None = None,
) -> tuple[rf.Network, dict[str, Any]]:
    """Merge two matched-port AC sweeps into a measured two-port matrix."""
    first = _extract_excitation_column(
        port1_raw_path,
        port_map=port_map,
        driven_port=1,
        z0=z0,
        source_resistor=port1_source_resistor,
        dialect=dialect,
    )
    second = _extract_excitation_column(
        port2_raw_path,
        port_map=port_map,
        driven_port=2,
        z0=z0,
        source_resistor=port2_source_resistor,
        dialect=dialect,
    )
    if first.freq_hz.shape != second.freq_hz.shape or not np.allclose(
        first.freq_hz, second.freq_hz, rtol=1e-10, atol=0.0
    ):
        raise ValueError(
            "Port-1 and port-2 sweeps use different frequency grids; "
            "refusing to interpolate independent simulations"
        )

    s = np.empty((first.freq_hz.size, 2, 2), dtype=np.complex128)
    s[:, :, 0] = first.column
    s[:, :, 1] = second.column
    net = rf.Network(
        frequency=rf.Frequency.from_f(first.freq_hz, unit="Hz"),
        s=s,
        z0=z0,
        name=f"{Path(port1_raw_path).stem}_two_sweep",
    )
    provenance = {
        "extraction_method": "two_excitation_power_waves",
        "source_files": {
            "port1_raw": str(Path(port1_raw_path).resolve()),
            "port2_raw": str(Path(port2_raw_path).resolve()),
        },
        "port_fixture": {
            "port_map": port_map,
            "z0_ohm": z0,
            "source_voltage_ac_v": 1.0,
            "port1_source_resistor": port1_source_resistor,
            "port2_source_resistor": port2_source_resistor,
            "unexcited_port_termination_ohm": z0,
        },
        "assumptions": [
            "Each driven port uses a 1 V AC Thevenin source with series impedance Z0.",
            "The unexcited port is terminated directly in Z0.",
            "Both sweeps describe the same circuit and frequency grid.",
        ],
        "validation": {
            "frequency_grid_exact_match": True,
            "port1_current_magnitude_residual": first.current_magnitude_residual,
            "port2_current_magnitude_residual": second.current_magnitude_residual,
        },
    }
    return net, provenance


def extract_sparams_from_raw(
    raw_path: str | Path,
    *,
    port_map: dict[int, str],
    assume_reciprocal_symmetric: bool,
    z0: float = 50.0,
    dialect: str | None = None,
) -> rf.Network:
    """Explicit opt-in single-sweep extraction for symmetric reciprocal DUTs.

    This compatibility path measures S11/S21 and copies them to S22/S12.
    It refuses to run unless ``assume_reciprocal_symmetric=True`` is passed.
    General two-ports must use :func:`extract_two_sweep_sparams`.
    """
    if not assume_reciprocal_symmetric:
        raise ValueError(
            "A single sweep cannot produce a general two-port matrix. "
            "Use extract_two_sweep_sparams with port-1 and port-2 results."
        )
    first = _extract_excitation_column(
        raw_path,
        port_map=port_map,
        driven_port=1,
        z0=z0,
        source_resistor="Rs1",
        dialect=dialect,
    )
    s = np.empty((first.freq_hz.size, 2, 2), dtype=np.complex128)
    s[:, :, 0] = first.column
    s[:, 0, 1] = first.column[:, 1]
    s[:, 1, 1] = first.column[:, 0]
    return rf.Network(
        frequency=rf.Frequency.from_f(first.freq_hz, unit="Hz"),
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
