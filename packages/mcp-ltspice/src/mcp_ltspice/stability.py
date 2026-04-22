"""Two-port stability analysis: K-factor, Δ, μ-factor across frequency."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from rf_mcp_common.touchstone import read_touchstone


def stability_check(s2p_path: str | Path) -> dict[str, Any]:
    """Compute Rollett K-factor, |Δ|, and μ-factor for a 2-port .s2p file.

    Returns:
        - ``freq_hz``: frequency array
        - ``k_factor``: Rollett K(ω); K > 1 needed for unconditional stability
        - ``delta_mag``: |Δ|; must be < 1 alongside K > 1
        - ``mu_factor``: Edwards-Sinsky μ; > 1 ⇒ unconditionally stable
        - ``unconditionally_stable``: True if K > 1 and |Δ| < 1 over the full sweep
        - ``min_k``, ``max_delta``: worst-case scalars
    """
    net = read_touchstone(s2p_path)
    s = net.s
    s11, s12, s21, s22 = s[:, 0, 0], s[:, 0, 1], s[:, 1, 0], s[:, 1, 1]
    delta = s11 * s22 - s12 * s21
    denom_k = 2.0 * np.abs(s12 * s21) + 1e-30
    k = (1 - np.abs(s11) ** 2 - np.abs(s22) ** 2 + np.abs(delta) ** 2) / denom_k
    mu = (1 - np.abs(s11) ** 2) / (
        np.abs(s22 - np.conj(s11) * delta) + np.abs(s12 * s21) + 1e-30
    )
    return {
        "freq_hz": net.f.tolist(),
        "k_factor": k.real.tolist(),
        "delta_mag": np.abs(delta).tolist(),
        "mu_factor": mu.real.tolist(),
        "min_k": float(np.min(k.real)),
        "max_delta": float(np.max(np.abs(delta))),
        "unconditionally_stable": bool(np.all((k.real > 1) & (np.abs(delta) < 1))),
    }
