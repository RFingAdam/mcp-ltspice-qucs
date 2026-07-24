"""Shared circuit identity and frequency-policy for analytical filter workflows.

The component dictionary alone is not enough to reconstruct a ladder: the
same reference/value mapping can represent different filter transforms and
topologies.  This module makes that context explicit and centralises spec-grid
construction so required criteria cannot be silently skipped.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, cast

import numpy as np
from numpy.typing import NDArray

from mcp_ltspice.eval import FilterSpec
from mcp_ltspice.extract import (
    ElementType,
    attach_component_parasitics,
    components_dict_to_elements,
    ladder_sparams_from_components,
)
from mcp_ltspice.synthesis import Topology

FilterKind = Literal["lowpass", "highpass", "bandpass", "bandstop"]
EvaluationMode = Literal["analytical", "approximate_model", "simulator_validated"]

_FILTER_KINDS = {"lowpass", "highpass", "bandpass", "bandstop"}


@dataclass(frozen=True)
class FilterAnalysisContext:
    """The information required to interpret and evaluate filter components."""

    kind: FilterKind = "lowpass"
    topology: Topology = Topology.SERIES_FIRST
    z0: float = 50.0
    transmission_zeros: bool | None = None
    evaluation_mode: EvaluationMode = "analytical"
    model_fidelity: str = "ideal_lumped"
    provenance: dict[str, Any] = field(default_factory=dict)
    component_substitution: dict[str, dict[str, Any]] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.kind not in _FILTER_KINDS:
            raise ValueError(f"kind must be one of {sorted(_FILTER_KINDS)}, got {self.kind!r}")
        if self.z0 <= 0 or not np.isfinite(self.z0):
            raise ValueError(f"z0 must be finite and > 0, got {self.z0!r}")

    @classmethod
    def create(
        cls,
        *,
        kind: FilterKind | str = "lowpass",
        topology: Topology | str = Topology.SERIES_FIRST,
        z0: float = 50.0,
        transmission_zeros: bool | None = None,
        evaluation_mode: EvaluationMode = "analytical",
        model_fidelity: str = "ideal_lumped",
        provenance: Mapping[str, Any] | None = None,
        component_substitution: dict[str, dict[str, Any]] | None = None,
    ) -> FilterAnalysisContext:
        """Validate string-facing API values and construct a typed context."""
        if kind not in _FILTER_KINDS:
            raise ValueError(f"kind must be one of {sorted(_FILTER_KINDS)}, got {kind!r}")
        try:
            parsed_topology = Topology(topology)
        except ValueError as exc:
            raise ValueError(
                f"topology must be one of {[item.value for item in Topology]}, got {topology!r}"
            ) from exc
        if component_substitution is not None and evaluation_mode == "analytical":
            evaluation_mode = "approximate_model"
        if component_substitution is not None and model_fidelity == "ideal_lumped":
            model_fidelity = "first_order_parasitic_reduction"
        return cls(
            kind=cast(FilterKind, kind),
            topology=parsed_topology,
            z0=z0,
            transmission_zeros=transmission_zeros,
            evaluation_mode=evaluation_mode,
            model_fidelity=model_fidelity,
            provenance=dict(provenance or {}),
            component_substitution=component_substitution,
        )

    def elements(self, components: dict[str, float]) -> list[tuple[ElementType, dict[str, float]]]:
        """Interpret a synthesis component mapping using this context."""
        elements = components_dict_to_elements(
            components,
            topology=self.topology.value,
            transmission_zeros=self.transmission_zeros,
            kind=self.kind,
        )
        if self.component_substitution is None:
            return elements
        return attach_component_parasitics(elements, components, self.component_substitution)

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["topology"] = self.topology.value
        substitution = result.pop("component_substitution")
        if substitution is not None:
            result["component_models"] = {
                refdes: {
                    "vendor": selected.get("vendor"),
                    "model_kind": selected.get("model", {}).get("model_kind"),
                    "checksum_sha256": selected.get("model", {}).get("checksum_sha256"),
                }
                for refdes, selected in substitution.items()
            }
        return result


@dataclass(frozen=True)
class MarginEvaluation:
    margins: dict[str, float]
    measured: dict[str, float]

    @property
    def overall(self) -> Literal["pass", "fail"]:
        return "pass" if all(value >= 0 for value in self.margins.values()) else "fail"


def build_spec_frequency_grid(
    spec: FilterSpec,
    *,
    npoints: int = 401,
    transmission_zeros_hz: list[float] | tuple[float, ...] = (),
    simulator_points_hz: list[float] | tuple[float, ...] = (),
) -> NDArray[np.float64]:
    """Build a positive grid containing every frequency required by ``spec``.

    A global logarithmic grid is augmented with a passband-local grid and the
    exact passband edges, stopband targets, transmission zeros, and supplied
    simulator points. This remains reliable for HPF/BSF specs whose stopband
    lies below the passband and for narrow BPF passbands.
    """
    if npoints < 8:
        raise ValueError(f"npoints must be >= 8, got {npoints}")

    pb = spec.passband
    if pb.f_stop <= pb.f_start:
        raise ValueError(f"passband f_stop must exceed f_start, got [{pb.f_start}, {pb.f_stop}]")

    if any(not np.isfinite(value) or value < 0 for value in transmission_zeros_hz):
        raise ValueError("transmission-zero frequencies must be finite and >= 0")
    if any(not np.isfinite(value) or value <= 0 for value in simulator_points_hz):
        raise ValueError("simulator frequencies must be finite and > 0")

    exact = [
        pb.f_stop,
        *(target.freq for target in spec.stopband_targets),
        *(value for value in transmission_zeros_hz if value > 0),
        *simulator_points_hz,
    ]
    if pb.f_start > 0:
        exact.append(pb.f_start)
    smallest_required = min(exact)
    largest_required = max(exact)
    start = min(pb.f_start, smallest_required) if pb.f_start > 0 else smallest_required / 1e4
    stop = max(pb.f_stop * 5.0, largest_required * 1.5)
    global_grid = np.geomspace(start, stop, npoints)

    pb_start = pb.f_start if pb.f_start > 0 else start
    passband_grid = np.geomspace(pb_start, pb.f_stop, max(32, npoints // 3))
    combined = np.concatenate(
        [
            global_grid,
            passband_grid,
            np.asarray(exact, dtype=float),
        ]
    )
    grid = np.unique(combined)
    validate_spec_frequency_coverage(spec, grid)
    return cast(NDArray[np.float64], grid)


def validate_spec_frequency_coverage(spec: FilterSpec, f_grid: np.ndarray) -> None:
    """Raise if any mandatory spec criterion cannot be evaluated on ``f_grid``."""
    if f_grid.ndim != 1 or f_grid.size < 2:
        raise ValueError("frequency grid must be a one-dimensional array with >= 2 points")
    if not np.all(np.isfinite(f_grid)) or np.any(f_grid <= 0) or np.any(np.diff(f_grid) <= 0):
        raise ValueError("frequency grid must be finite, positive, and strictly increasing")

    pb = spec.passband
    pb_mask = (f_grid >= pb.f_start) & (f_grid <= pb.f_stop)
    if not pb_mask.any():
        raise ValueError(
            f"passband [{pb.f_start}, {pb.f_stop}] Hz is outside frequency grid "
            f"[{f_grid[0]}, {f_grid[-1]}] Hz"
        )
    for target in spec.stopband_targets:
        if target.freq < f_grid[0] or target.freq > f_grid[-1]:
            raise ValueError(
                f"required stopband target {target.label!r} at {target.freq} Hz is outside "
                f"frequency grid [{f_grid[0]}, {f_grid[-1]}] Hz"
            )


def evaluate_component_margins(
    components: dict[str, float],
    spec: FilterSpec,
    *,
    context: FilterAnalysisContext,
    f_grid: np.ndarray,
) -> MarginEvaluation:
    """Evaluate every spec criterion; missing/non-finite results are errors."""
    validate_spec_frequency_coverage(spec, f_grid)
    elements = context.elements(components)
    s = ladder_sparams_from_components(elements, f_grid, z0=context.z0)
    s21_db = 20 * np.log10(np.maximum(np.abs(s[:, 1, 0]), 1e-12))
    s11_db = 20 * np.log10(np.maximum(np.abs(s[:, 0, 0]), 1e-12))
    if not np.all(np.isfinite(s21_db)) or not np.all(np.isfinite(s11_db)):
        raise ValueError("filter response contains non-finite S-parameter values")

    pb = spec.passband
    pb_mask = (f_grid >= pb.f_start) & (f_grid <= pb.f_stop)
    worst_il = float(-s21_db[pb_mask].min())
    worst_rl = float(-s11_db[pb_mask].max())
    margins = {
        "Passband IL": pb.il_max_db - worst_il,
        "Passband RL": worst_rl - pb.rl_min_db,
    }
    measured = {
        "Passband IL": worst_il,
        "Passband RL": worst_rl,
    }
    for target in spec.stopband_targets:
        rejection = -float(np.interp(target.freq, f_grid, s21_db))
        if not np.isfinite(rejection):
            raise ValueError(f"criterion {target.label!r} produced a non-finite result")
        margins[target.label] = rejection - target.rejection_min_db
        measured[target.label] = rejection

    return MarginEvaluation(margins=margins, measured=measured)
