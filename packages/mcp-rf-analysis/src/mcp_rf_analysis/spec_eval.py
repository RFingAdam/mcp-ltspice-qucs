"""Spec evaluation against curated regulatory templates."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

import numpy as np
from rf_mcp_common.touchstone import read_touchstone

from mcp_rf_analysis.network_ops import s21_db_at


def check_rejection_at(
    s2p_path: str | Path, freq_hz: float, min_rejection_db: float
) -> dict[str, Any]:
    """Check that |S21| at a given frequency is at or below the rejection target.

    Rejection is reported as a positive number (e.g. 50 dB rejection means
    |S21| ≤ -50 dB).
    """
    net = read_touchstone(s2p_path)
    if freq_hz < net.f.min() or freq_hz > net.f.max():
        return {
            "freq_hz": freq_hz,
            "min_rejection_db": min_rejection_db,
            "measured_rejection_db": float("nan"),
            "margin_db": float("nan"),
            "status": "out_of_sweep",
        }
    s21_db = s21_db_at(net, freq_hz)
    rejection = -s21_db
    return {
        "freq_hz": freq_hz,
        "min_rejection_db": min_rejection_db,
        "measured_rejection_db": rejection,
        "margin_db": rejection - min_rejection_db,
        "status": "pass" if rejection >= min_rejection_db else "fail",
    }


def check_passband_compliance(
    s2p_path: str | Path,
    f_start: float,
    f_stop: float,
    *,
    il_max_db: float,
    rl_min_db: float,
) -> dict[str, Any]:
    """Check passband insertion loss + return loss across [f_start, f_stop]."""
    net = read_touchstone(s2p_path)
    mask = (net.f >= f_start) & (net.f <= f_stop)
    if not mask.any():
        return {"status": "out_of_sweep"}
    s21_db = 20 * np.log10(np.maximum(np.abs(net.s[mask, 1, 0]), 1e-12))
    s11_db = 20 * np.log10(np.maximum(np.abs(net.s[mask, 0, 0]), 1e-12))
    worst_il = float(-s21_db.min())
    worst_rl = float(-s11_db.max())
    return {
        "f_start": f_start,
        "f_stop": f_stop,
        "il_max_db": il_max_db,
        "rl_min_db": rl_min_db,
        "measured_worst_il_db": worst_il,
        "measured_worst_rl_db": worst_rl,
        "il_margin_db": il_max_db - worst_il,
        "rl_margin_db": worst_rl - rl_min_db,
        "status": (
            "pass"
            if worst_il <= il_max_db and worst_rl >= rl_min_db
            else "fail"
        ),
    }


def list_spec_templates() -> list[str]:
    """Return the names of bundled spec templates."""
    pkg = resources.files("mcp_rf_analysis").joinpath("resources/templates")
    return sorted(p.name.removesuffix(".json") for p in pkg.iterdir() if p.suffix == ".json")


def evaluate_against_spec_template(
    s2p_path: str | Path, template_name: str
) -> dict[str, Any]:
    """Load a spec template by name and evaluate the .s2p against it."""
    pkg = resources.files("mcp_rf_analysis").joinpath("resources/templates")
    path = pkg.joinpath(f"{template_name}.json")
    with path.open("r", encoding="utf-8") as f:
        spec = json.load(f)

    results: list[dict[str, Any]] = []

    if "passband" in spec:
        pb = spec["passband"]
        results.append(
            {
                "label": "passband",
                **check_passband_compliance(
                    s2p_path, pb["f_start"], pb["f_stop"],
                    il_max_db=pb["il_max_db"], rl_min_db=pb["rl_min_db"],
                ),
            }
        )

    for tgt in spec.get("stopband_targets", []):
        results.append(
            {
                "label": tgt.get("label", f"stopband@{tgt['freq']/1e6:.1f}MHz"),
                **check_rejection_at(s2p_path, tgt["freq"], tgt["rejection_min_db"]),
            }
        )

    overall = "pass" if all(r.get("status") == "pass" for r in results) else "fail"
    return {
        "template": template_name,
        "spec": spec,
        "criteria": results,
        "overall": overall,
    }
