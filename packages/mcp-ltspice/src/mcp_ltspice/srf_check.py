"""SRF-aware sanity check for filter component selections.

When a stopband target frequency exceeds the self-resonant frequency
(SRF) of any inductor in the design, the analytical model is not
predictive — above SRF, an inductor looks capacitive and the lossless
ABCD-chain math diverges from real measurements.

This module flags such mismatches so the user can either:

1. Move the part to a series with higher SRF (e.g. Coilcraft 0402DC
   instead of 0402HP),
2. Move the trap to a different ladder position with a smaller value
   (smaller L → higher SRF), or
3. Accept that the deep-stopband performance will be measurement-
   dominated, not predictable from synthesis.
"""

from __future__ import annotations

from typing import Any

from mcp_ltspice.eval import FilterSpec
from mcp_ltspice.vendor_models import lookup_part


def srf_audit(
    components: dict[str, float],
    spec: FilterSpec | dict[str, Any],
    *,
    inductor_vendor: str = "coilcraft_0402hp",
    capacitor_vendor: str = "murata_gjm_c0g",
    margin_pct: float = 30.0,
) -> dict[str, Any]:
    """Identify components whose SRF is too close to a spec target.

    A component is flagged if any spec frequency exceeds
    ``srf * (1 - margin_pct/100)``. Default 30% margin means we flag
    anything whose SRF is less than 1.43× the highest spec frequency.

    Returns a dict with:
    - ``warnings``: list of human-readable strings
    - ``per_component``: dict of refdes → {srf_hz, max_target_hz, ratio,
      flagged}
    - ``severity``: 'ok' | 'caution' | 'critical'
    """
    if isinstance(spec, dict):
        spec = FilterSpec.model_validate(spec)

    spec_freqs = [spec.passband.f_stop]
    spec_freqs.extend(t.freq for t in spec.stopband_targets)
    f_max_target = max(spec_freqs)

    threshold_factor = 1.0 - margin_pct / 100.0
    per_component: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    unaudited: list[str] = []
    for refdes, value in components.items():
        kind = "L" if refdes.startswith("L") else "C"
        vendor = inductor_vendor if kind == "L" else capacitor_vendor
        try:
            part = lookup_part(vendor, value, kind=kind)  # type: ignore[arg-type]
        except (ValueError, KeyError) as exc:
            # Surface as a warning rather than silently dropping the
            # component from the audit — otherwise an out-of-catalog part
            # masquerades as "no concerns".
            unaudited.append(refdes)
            warnings.append(
                f"{refdes} = {value:.3e} not found in {vendor} catalog "
                f"({exc.__class__.__name__}: {exc}); SRF unknown — "
                f"audit incomplete for this component."
            )
            continue
        srf = part.srf_hz
        ratio = f_max_target / srf
        flagged = srf * threshold_factor < f_max_target
        per_component[refdes] = {
            "value": value,
            "srf_hz": srf,
            "max_spec_target_hz": f_max_target,
            "spec_to_srf_ratio": ratio,
            "flagged": flagged,
            "vendor": vendor,
        }
        if flagged:
            warnings.append(
                f"{refdes} = {value:.3e} ({vendor}) has SRF "
                f"{srf / 1e9:.2f} GHz — within {margin_pct}% of the highest "
                f"spec target ({f_max_target / 1e9:.2f} GHz). Analytical "
                f"rejection at high frequencies will not match measurement."
            )

    n_flagged = sum(1 for c in per_component.values() if c["flagged"])
    if n_flagged == 0 and not unaudited:
        severity = "ok"
    elif n_flagged == 0 and unaudited:
        severity = "caution"  # something we couldn't audit — caller should know
    elif n_flagged <= 2:
        severity = "caution"
    else:
        severity = "critical"

    return {
        "n_components": len(per_component),
        "n_flagged": n_flagged,
        "n_unaudited": len(unaudited),
        "unaudited": unaudited,
        "severity": severity,
        "warnings": warnings,
        "per_component": per_component,
        "max_spec_target_hz": f_max_target,
        "margin_pct": margin_pct,
    }
