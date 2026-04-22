"""Transmission-zero placement for shunt LC traps in elliptic ladders."""

from __future__ import annotations

import math

from rf_mcp_common.ecomp import ESeries, snap_to_eseries


def trap_lc_for_freq(
    target_freq_hz: float,
    *,
    l_existing: float | None = None,
    c_existing: float | None = None,
    preserve_ratio: bool = True,
) -> tuple[float, float]:
    """Compute (L, C) values for a shunt LC trap to resonate at
    ``target_freq_hz``.

    A series LC has resonance at f₀ = 1 / (2π√(LC)).

    - If ``preserve_ratio=True`` and both ``l_existing`` and ``c_existing``
      are provided, scale both L and C by the same factor so their ratio
      (and thus impedance match into the ladder) is preserved.
    - Otherwise, if only one of L/C is provided, hold it fixed and solve
      for the other.
    """
    if target_freq_hz <= 0:
        raise ValueError(f"target_freq_hz must be >0, got {target_freq_hz}")

    omega_target_sq = (2.0 * math.pi * target_freq_hz) ** 2

    if preserve_ratio and l_existing is not None and c_existing is not None:
        # Old resonance: ω_old² = 1/(L_old C_old)
        # New target:    ω_new² = 1/(L_new C_new)
        # Preserve L/C: L_new = α L_old, C_new = α C_old
        # → ω_new² = 1/(α² L_old C_old) → α = ω_old / ω_new
        omega_old_sq = 1.0 / (l_existing * c_existing)
        alpha = math.sqrt(omega_old_sq / omega_target_sq)
        return l_existing * alpha, c_existing * alpha

    if c_existing is not None and l_existing is None:
        l_new = 1.0 / (omega_target_sq * c_existing)
        return l_new, c_existing

    if l_existing is not None and c_existing is None:
        c_new = 1.0 / (omega_target_sq * l_existing)
        return l_existing, c_new

    # Neither given: pick a reasonable L (1 nH) and solve for C
    l_default = 1e-9
    c_new = 1.0 / (omega_target_sq * l_default)
    return l_default, c_new


def set_trap_frequency(
    components: dict[str, float],
    trap_index: int,
    target_freq_hz: float,
    *,
    preserve_ratio: bool = True,
    snap_series: ESeries | str | None = ESeries.E24,
) -> dict[str, float]:
    """Update a components dict so trap ``trap_index`` resonates at
    ``target_freq_hz``. Returns a new dict; original is not modified.

    Trap index refers to the L/C pair labels (L2/C2, L4/C4, L6/C6, …).
    """
    new_comps = dict(components)
    l_key = f"L{trap_index}"
    c_key = f"C{trap_index}"
    if l_key not in new_comps or c_key not in new_comps:
        raise KeyError(
            f"Trap {trap_index} not found in components "
            f"(missing {l_key} and/or {c_key})"
        )

    l_new, c_new = trap_lc_for_freq(
        target_freq_hz,
        l_existing=new_comps[l_key],
        c_existing=new_comps[c_key],
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
    preserve_ratio: bool = True,
    snap_series: ESeries | str | None = ESeries.E24,
) -> dict[str, object]:
    """User-facing wrapper. Returns updated components plus diagnostic
    info (achieved resonance, snap errors, original values)."""
    if trap_index <= 0:
        raise ValueError(f"trap_index must be positive, got {trap_index}")

    l_key = f"L{trap_index}"
    c_key = f"C{trap_index}"
    l_old = components.get(l_key)
    c_old = components.get(c_key)
    if l_old is None or c_old is None:
        raise KeyError(f"Trap {trap_index} (need {l_key}+{c_key}) not in components")

    new = set_trap_frequency(
        components, trap_index, target_freq_hz,
        preserve_ratio=preserve_ratio, snap_series=snap_series,
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
