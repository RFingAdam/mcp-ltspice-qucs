"""Spec evaluation: pass/fail per criterion with margin in dB."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
import skrf as rf
from numpy.typing import NDArray
from pydantic import BaseModel, Field

from rf_mcp_common.touchstone import read_touchstone


class PassbandSpec(BaseModel):
    """Maximum insertion loss + minimum return loss across a frequency band."""

    f_start: float = Field(ge=0, description="Lower band edge in Hz")
    f_stop: float = Field(gt=0, description="Upper band edge in Hz")
    il_max_db: float = Field(gt=0, description="Max allowed insertion loss in dB (positive)")
    rl_min_db: float = Field(gt=0, description="Min required return loss in dB (positive)")


class StopbandTarget(BaseModel):
    """Single-frequency rejection target."""

    freq: float = Field(gt=0, description="Frequency in Hz")
    rejection_min_db: float = Field(gt=0, description="Minimum rejection in dB (positive)")
    label: str = Field(description="Human-readable identifier (e.g., 'EU 2f0')")


class FilterSpec(BaseModel):
    """Coexistence-aware filter spec."""

    passband: PassbandSpec
    stopband_targets: list[StopbandTarget] = Field(default_factory=list)


class CriterionResult(BaseModel):
    label: str
    metric: str
    target_db: float
    measured_db: float
    margin_db: float
    status: Literal["pass", "fail"]


class SpecEvalResult(BaseModel):
    overall: Literal["pass", "fail"]
    criteria: list[CriterionResult]
    s2p_path: str
    spec: FilterSpec


def _s21_db_at(net: rf.Network, freq_hz: float) -> float:
    """Linearly-interpolated |S21| in dB."""
    s21 = np.interp(freq_hz, net.f, net.s[:, 1, 0])
    return float(20.0 * np.log10(max(abs(s21), 1e-12)))


def _s11_db_at(net: rf.Network, freq_hz: float) -> float:
    s11 = np.interp(freq_hz, net.f, net.s[:, 0, 0])
    return float(20.0 * np.log10(max(abs(s11), 1e-12)))


def _band_mask(net: rf.Network, f_start: float, f_stop: float) -> NDArray[np.bool_]:
    return cast(NDArray[np.bool_], (net.f >= f_start) & (net.f <= f_stop))


def evaluate_filter_spec(
    s2p_path: str | Path,
    spec: FilterSpec | dict[str, Any],
) -> SpecEvalResult:
    """Evaluate a Touchstone S₂ₚ file against a spec.

    Returns ``overall`` = "pass" only if every criterion passes, plus a
    detailed per-criterion result with measured value and dB margin.

    Sign convention for margin:
    - For maxima (IL): margin = target - measured. Positive = pass.
    - For minima (RL, rejection): margin = measured - target. Positive = pass.
    """
    if isinstance(spec, dict):
        spec = FilterSpec.model_validate(spec)

    net = read_touchstone(s2p_path)
    if net.nports != 2:
        raise ValueError(f"Spec evaluation requires a 2-port network, got {net.nports}")

    criteria: list[CriterionResult] = []

    pb = spec.passband
    pb_mask = _band_mask(net, pb.f_start, pb.f_stop)
    if not pb_mask.any():
        raise ValueError(
            f"Passband [{pb.f_start}, {pb.f_stop}] Hz outside Touchstone sweep "
            f"[{net.f.min()}, {net.f.max()}] Hz"
        )
    s21_pb_db = 20.0 * np.log10(np.maximum(np.abs(net.s[pb_mask, 1, 0]), 1e-12))
    s11_pb_db = 20.0 * np.log10(np.maximum(np.abs(net.s[pb_mask, 0, 0]), 1e-12))
    worst_il = -float(s21_pb_db.min())  # IL is positive number; use most-negative S21
    worst_rl = -float(s11_pb_db.max())  # RL is positive number; use least-negative S11
    criteria.append(
        CriterionResult(
            label="Passband IL",
            metric=f"|S21| over [{pb.f_start / 1e6:.0f}–{pb.f_stop / 1e6:.0f}] MHz",
            target_db=pb.il_max_db,
            measured_db=worst_il,
            margin_db=pb.il_max_db - worst_il,
            status="pass" if worst_il <= pb.il_max_db else "fail",
        )
    )
    criteria.append(
        CriterionResult(
            label="Passband RL",
            metric=f"|S11| over [{pb.f_start / 1e6:.0f}–{pb.f_stop / 1e6:.0f}] MHz",
            target_db=pb.rl_min_db,
            measured_db=worst_rl,
            margin_db=worst_rl - pb.rl_min_db,
            status="pass" if worst_rl >= pb.rl_min_db else "fail",
        )
    )

    for tgt in spec.stopband_targets:
        if tgt.freq < net.f.min() or tgt.freq > net.f.max():
            criteria.append(
                CriterionResult(
                    label=tgt.label,
                    metric=f"|S21| at {tgt.freq / 1e6:.1f} MHz",
                    target_db=tgt.rejection_min_db,
                    measured_db=float("nan"),
                    margin_db=float("nan"),
                    status="fail",
                )
            )
            continue
        s21_db = _s21_db_at(net, tgt.freq)
        rejection = -s21_db  # rejection is positive
        criteria.append(
            CriterionResult(
                label=tgt.label,
                metric=f"|S21| at {tgt.freq / 1e6:.1f} MHz",
                target_db=tgt.rejection_min_db,
                measured_db=rejection,
                margin_db=rejection - tgt.rejection_min_db,
                status="pass" if rejection >= tgt.rejection_min_db else "fail",
            )
        )

    overall: Literal["pass", "fail"] = (
        "pass" if all(c.status == "pass" for c in criteria) else "fail"
    )
    return SpecEvalResult(
        overall=overall,
        criteria=criteria,
        s2p_path=str(Path(s2p_path).resolve()),
        spec=spec,
    )
