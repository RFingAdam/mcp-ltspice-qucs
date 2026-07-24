"""Instantiate selected passive models into simulator-ready SPICE netlists."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from mcp_ltspice.extract import (
    ElementType,
    associate_element_refdes,
    components_dict_to_elements,
)
from mcp_ltspice.synthesis import Topology


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", text)


def _approx_subcircuit(refdes: str, selected: dict[str, Any]) -> tuple[str, list[str]]:
    name = f"RFMC_{_safe_name(refdes)}"
    value = float(selected["snapped_value"])
    resistance = max(float(selected.get("Rs", 0.0)), 1e-12)
    if selected["kind"] == "L":
        parasitic = float(selected["Cp"])
        lines = [
            f".SUBCKT {name} p n",
            f"Rloss p x {resistance:.12g}",
            f"Lmain x n {value:.12g}",
            f"Cpar p n {parasitic:.12g}",
            f".ENDS {name}",
        ]
    else:
        parasitic = float(selected["Ls"])
        lines = [
            f".SUBCKT {name} p n",
            f"Rloss p x {resistance:.12g}",
            f"Lpar x y {parasitic:.12g}",
            f"Cmain y n {value:.12g}",
            f".ENDS {name}",
        ]
    return name, lines


def _instance(
    refdes: str,
    node_a: str,
    node_b: str,
    selected: dict[str, Any],
    definitions: list[str],
    includes: set[str],
) -> str:
    model = selected["model"]
    if model["model_kind"] == "subckt":
        source_path = model.get("source_path")
        subcircuit = model.get("subcircuit_name")
        if not source_path or not subcircuit:
            raise ValueError(f"{refdes}: subckt model lacks source_path or subcircuit_name")
        includes.add(str(Path(source_path).resolve()))
        return f"X{_safe_name(refdes)} {node_a} {node_b} {subcircuit}"

    # Curated models and measured Touchstone parts currently use the explicit
    # first-order reduction recorded in their model provenance.
    subcircuit, lines = _approx_subcircuit(refdes, selected)
    definitions.extend(lines)
    return f"X{_safe_name(refdes)} {node_a} {node_b} {subcircuit}"


def generate_realized_filter_netlist(
    substitution: dict[str, dict[str, Any]],
    output_path: str | Path,
    *,
    kind: str = "lowpass",
    topology: Topology | str = Topology.SERIES_FIRST,
    transmission_zeros: bool | None = None,
    driven_port: Literal[1, 2] = 1,
    z0: float = 50.0,
    f_start_hz: float = 1e6,
    f_stop_hz: float = 5e9,
    points_per_decade: int = 200,
) -> tuple[Path, Path]:
    """Write a model-backed LPF/HPF/BPF/BSF ladder SPICE netlist."""
    if not substitution:
        raise ValueError("substitution cannot be empty")
    if z0 <= 0 or not 0 < f_start_hz < f_stop_hz or points_per_decade < 1:
        raise ValueError("invalid impedance or frequency sweep")

    components = {
        refdes: float(selected["snapped_value"]) for refdes, selected in substitution.items()
    }
    elements = components_dict_to_elements(
        components,
        topology=Topology(topology).value,
        transmission_zeros=transmission_zeros,
        kind=kind,
    )
    associations = associate_element_refdes(elements, components)
    series_kinds: set[ElementType] = {
        "series_l",
        "series_c",
        "series_lc_series",
        "series_lc_parallel",
    }
    n_series = sum(element_kind in series_kinds for element_kind, _ in elements)

    definitions: list[str] = []
    includes: set[str] = set()
    body: list[str] = []
    current = "p1"
    series_seen = 0

    def add(refdes: str, node_a: str, node_b: str) -> None:
        body.append(
            _instance(
                refdes,
                node_a,
                node_b,
                substitution[refdes],
                definitions,
                includes,
            )
        )

    for element_index, ((element_kind, _params), refs) in enumerate(
        zip(elements, associations, strict=True), start=1
    ):
        next_node: str | None = None
        if element_kind in series_kinds:
            series_seen += 1
            next_node = "p2" if series_seen == n_series else f"n{element_index}"

        if element_kind in {"series_l", "series_c"}:
            role = "L" if element_kind == "series_l" else "C"
            assert next_node is not None
            add(refs[role], current, next_node)
            current = next_node
        elif element_kind in {"shunt_l", "shunt_c"}:
            role = "L" if element_kind == "shunt_l" else "C"
            add(refs[role], current, "0")
        elif element_kind == "shunt_lc_trap":
            middle = f"trap{element_index}"
            add(refs["L"], current, middle)
            add(refs["C"], middle, "0")
        elif element_kind == "series_lc_series":
            assert next_node is not None
            middle = f"series{element_index}"
            add(refs["L"], current, middle)
            add(refs["C"], middle, next_node)
            current = next_node
        elif element_kind == "shunt_lc_parallel":
            add(refs["L"], current, "0")
            add(refs["C"], current, "0")
        elif element_kind == "series_lc_parallel":
            assert next_node is not None
            add(refs["L"], current, next_node)
            add(refs["C"], current, next_node)
            current = next_node
        elif element_kind == "shunt_composite_trap":
            middle1 = f"composite_a{element_index}"
            middle2 = f"composite_b{element_index}"
            add(refs["L_s"], current, middle1)
            add(refs["C_s"], middle1, middle2)
            add(refs["L_p"], middle2, "0")
            add(refs["C_p"], middle2, "0")
        else:
            raise ValueError(f"Unsupported realized element {element_kind!r}")

    if current != "p2":
        body.append("Rport_link p1 p2 1e-12")

    fixture = (
        [
            "V1 src1 0 AC 1",
            f"Rs1 src1 p1 {z0:.12g}",
            f"RL1 p2 0 {z0:.12g}",
        ]
        if driven_port == 1
        else [
            "V1 src1 0 AC 0",
            f"Rs1 src1 p1 {z0:.12g}",
            "V2 src2 0 AC 1",
            f"RL1 src2 p2 {z0:.12g}",
        ]
    )
    lines = [
        "* realized filter generated by mcp-ltspice",
        *[f'.include "{path}"' for path in sorted(includes)],
        *definitions,
        *fixture,
        *body,
        f".ac dec {points_per_decade} {f_start_hz:.12g} {f_stop_hz:.12g}",
        ".save V(p1) V(p2)",
        ".end",
    ]
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")

    manifest = output.with_suffix(".models.json")
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "evaluation_mode": "simulator_model",
                "kind": kind,
                "topology": Topology(topology).value,
                "transmission_zeros": transmission_zeros,
                "driven_port": driven_port,
                "netlist_path": str(output),
                "components": substitution,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return output, manifest


def generate_realized_lpf_netlist(
    substitution: dict[str, dict[str, Any]],
    output_path: str | Path,
    *,
    driven_port: Literal[1, 2] = 1,
    z0: float = 50.0,
    f_start_hz: float = 1e6,
    f_stop_hz: float = 5e9,
    points_per_decade: int = 200,
) -> tuple[Path, Path]:
    """Backward-compatible series-first low-pass realization wrapper."""
    return generate_realized_filter_netlist(
        substitution,
        output_path,
        kind="lowpass",
        topology=Topology.SERIES_FIRST,
        driven_port=driven_port,
        z0=z0,
        f_start_hz=f_start_hz,
        f_stop_hz=f_stop_hz,
        points_per_decade=points_per_decade,
    )
