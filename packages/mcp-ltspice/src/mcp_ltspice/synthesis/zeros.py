"""Transmission-zero placement for shunt LC traps in elliptic ladders."""

from __future__ import annotations

import math
import warnings
from typing import Literal

from rf_mcp_common.ecomp import ESeries, snap_to_eseries

TrapMode = Literal["preserve_ratio", "hold_l", "hold_c"]


def _resolve_mode(
    mode: TrapMode | None,
    preserve_ratio: bool | None,
) -> TrapMode:
    """Map the legacy ``preserve_ratio: bool`` API onto the new ``mode``.

    Precedence:
    - If ``mode`` is given, use it (and warn if ``preserve_ratio`` is also passed).
    - Else if ``preserve_ratio`` is given, map ``True → "preserve_ratio"`` and
      ``False → "hold_c"`` (most common intent — hold C, recompute L).
    - Else default to ``"preserve_ratio"``.
    """
    if mode is not None and preserve_ratio is not None:
        warnings.warn(
            "Both `mode` and the legacy `preserve_ratio` were passed to a "
            "transmission-zero helper. `mode` wins; remove `preserve_ratio` "
            "from the call.",
            DeprecationWarning,
            stacklevel=3,
        )
        return mode
    if mode is not None:
        return mode
    if preserve_ratio is not None:
        warnings.warn(
            "`preserve_ratio: bool` is deprecated; use "
            "`mode: Literal['preserve_ratio', 'hold_l', 'hold_c']` instead. "
            "Mapping `True → 'preserve_ratio'`, `False → 'hold_c'`.",
            DeprecationWarning,
            stacklevel=3,
        )
        return "preserve_ratio" if preserve_ratio else "hold_c"
    return "preserve_ratio"


def trap_lc_for_freq(
    target_freq_hz: float,
    *,
    l_existing: float | None = None,
    c_existing: float | None = None,
    mode: TrapMode | None = None,
    preserve_ratio: bool | None = None,
) -> tuple[float, float]:
    """Compute (L, C) values for a shunt LC trap to resonate at ``target_freq_hz``.

    A series LC has resonance at ``f₀ = 1 / (2π√(LC))``.

    Modes:

    - ``"preserve_ratio"`` (default) — both ``l_existing`` and ``c_existing``
      are required. Both are scaled by the same factor so the L/C ratio
      (and thus impedance match into the ladder) is preserved.
    - ``"hold_l"`` — ``l_existing`` is required; ``c_existing`` is ignored
      if also passed. ``L`` is held fixed and ``C`` is recomputed.
    - ``"hold_c"`` — ``c_existing`` is required; ``l_existing`` is ignored
      if also passed. ``C`` is held fixed and ``L`` is recomputed. Often
      preferred when the cap value is already at a catalogue E-series and
      you'd rather drift the inductor.

    The legacy ``preserve_ratio: bool`` parameter is accepted for
    back-compat (with ``DeprecationWarning``); see :func:`_resolve_mode`
    for the mapping.

    Earlier versions had a bug where ``preserve_ratio=False`` with both
    ``l_existing`` and ``c_existing`` provided fell through none of the
    branches and silently substituted ``L=1 nH``. That fall-through is
    fixed.
    """
    if target_freq_hz <= 0:
        raise ValueError(f"target_freq_hz must be >0, got {target_freq_hz}")

    resolved = _resolve_mode(mode, preserve_ratio)

    # Smart default: when no explicit mode/preserve_ratio is given and
    # only one of L / C is provided, auto-select the matching hold mode.
    # Matches pre-mode-API behaviour where calling with just `l_existing`
    # implicitly held L.
    if mode is None and preserve_ratio is None:
        if l_existing is not None and c_existing is None:
            resolved = "hold_l"
        elif c_existing is not None and l_existing is None:
            resolved = "hold_c"

    omega_target_sq = (2.0 * math.pi * target_freq_hz) ** 2

    if resolved == "preserve_ratio":
        if l_existing is None or c_existing is None:
            raise ValueError(
                "mode='preserve_ratio' requires both l_existing and c_existing"
            )
        omega_old_sq = 1.0 / (l_existing * c_existing)
        alpha = math.sqrt(omega_old_sq / omega_target_sq)
        return l_existing * alpha, c_existing * alpha

    if resolved == "hold_l":
        if l_existing is None:
            raise ValueError("mode='hold_l' requires l_existing")
        c_new = 1.0 / (omega_target_sq * l_existing)
        return l_existing, c_new

    if resolved == "hold_c":
        if c_existing is None:
            raise ValueError("mode='hold_c' requires c_existing")
        l_new = 1.0 / (omega_target_sq * c_existing)
        return l_new, c_existing

    raise ValueError(f"Unknown trap mode: {resolved!r}")  # unreachable


def set_trap_frequency(
    components: dict[str, float],
    trap_index: int,
    target_freq_hz: float,
    *,
    mode: TrapMode | None = None,
    preserve_ratio: bool | None = None,
    snap_series: ESeries | str | None = ESeries.E24,
) -> dict[str, float]:
    """Update a components dict so trap ``trap_index`` resonates at
    ``target_freq_hz``. Returns a new dict; original is not modified.

    Trap index refers to the L/C pair labels (L2/C2, L4/C4, L6/C6, …).

    See :func:`trap_lc_for_freq` for ``mode`` semantics.
    """
    new_comps = dict(components)
    l_key = f"L{trap_index}"
    c_key = f"C{trap_index}"
    if l_key not in new_comps or c_key not in new_comps:
        raise KeyError(
            f"Trap {trap_index} not found in components (missing {l_key} and/or {c_key})"
        )

    l_new, c_new = trap_lc_for_freq(
        target_freq_hz,
        l_existing=new_comps[l_key],
        c_existing=new_comps[c_key],
        mode=mode,
        preserve_ratio=preserve_ratio,
    )

    if snap_series is not None:
        l_snap = snap_to_eseries(l_new, snap_series)
        c_snap = snap_to_eseries(c_new, snap_series)
        new_comps[l_key] = l_snap.snapped
        new_comps[c_key] = c_snap.snapped
    else:
        new_comps[l_key] = l_new
        new_comps[c_key] = c_new

    return new_comps


def place_transmission_zero(
    components: dict[str, float],
    trap_index: int,
    target_freq_hz: float,
    *,
    mode: TrapMode | None = None,
    preserve_ratio: bool | None = None,
    snap_series: ESeries | str | None = ESeries.E24,
) -> dict[str, object]:
    """User-facing wrapper. Returns updated components plus diagnostic
    info (achieved resonance, snap errors, original values).

    See :func:`trap_lc_for_freq` for ``mode`` semantics. The legacy
    ``preserve_ratio: bool`` argument is accepted with deprecation.
    """
    if trap_index <= 0:
        raise ValueError(f"trap_index must be positive, got {trap_index}")

    l_key = f"L{trap_index}"
    c_key = f"C{trap_index}"
    l_old = components.get(l_key)
    c_old = components.get(c_key)
    if l_old is None or c_old is None:
        raise KeyError(f"Trap {trap_index} (need {l_key}+{c_key}) not in components")

    new = set_trap_frequency(
        components,
        trap_index,
        target_freq_hz,
        mode=mode,
        preserve_ratio=preserve_ratio,
        snap_series=snap_series,
    )
    l_new = new[l_key]
    c_new = new[c_key]

    achieved_hz = 1.0 / (2.0 * math.pi * math.sqrt(l_new * c_new))

    return {
        "components": new,
        "trap_index": trap_index,
        "target_freq_hz": target_freq_hz,
        "achieved_freq_hz": achieved_hz,
        "freq_error_pct": (achieved_hz - target_freq_hz) / target_freq_hz * 100.0,
        "previous": {l_key: l_old, c_key: c_old},
        "new": {l_key: l_new, c_key: c_new},
    }
