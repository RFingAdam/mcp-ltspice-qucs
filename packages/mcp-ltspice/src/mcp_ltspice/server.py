"""FastMCP server entry point for mcp-ltspice."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import numpy as np
from fastmcp import FastMCP
from mcp_ltspice import __version__
from mcp_ltspice.asc_io import (
    generate_lpf_asc,
    read_components,
    update_component,
)
from mcp_ltspice.eval import FilterSpec, evaluate_filter_spec
from mcp_ltspice.extract import (
    components_dict_to_elements,
    extract_sparams_from_raw,
    ladder_sparams_from_components,
)
from mcp_ltspice.render import render_response as _render_response
from mcp_ltspice.runner import RunResult, Simulator, run_simulation as _run_simulation
from mcp_ltspice.synthesis import (
    Topology,
    place_transmission_zero as _place_transmission_zero,
    synthesize_lc_lpf,
)
from pydantic import Field
from rf_mcp_common.envelope import Envelope, Timer, error, ok
from rf_mcp_common.logging import get_logger
from rf_mcp_common.touchstone import network_to_touchstone, write_touchstone

mcp = FastMCP(
    name="mcp-ltspice",
    version=__version__,
)
log = get_logger("mcp_ltspice.server")


# ---------------------------------------------------------------------------
# Tool 1: run_simulation
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Run an LTspice / ngspice simulation headlessly. Returns the path "
        "to the generated .raw file. Auto-selects LTspice (via Wine) when "
        "available, falls back to ngspice."
    ),
)
def run_simulation(
    asc_path: Annotated[str, Field(description="Path to the .asc schematic.")],
    prefer: Annotated[
        str | None,
        Field(description="Force 'ltspice' or 'ngspice'. Default: auto."),
    ] = None,
    timeout_sec: Annotated[float, Field(gt=0, le=600)] = 120.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        prefer_enum = Simulator(prefer) if prefer else None
        result: RunResult = _run_simulation(
            asc_path, prefer=prefer_enum, timeout=timeout_sec
        )
        return ok(
            {
                "raw_path": str(result.raw_path),
                "log_path": str(result.log_path),
                "simulator": result.simulator.value,
                "returncode": result.returncode,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:  # noqa: BLE001 — tool boundary
        return error(f"run_simulation failed: {e}", tool_version=__version__)


# ---------------------------------------------------------------------------
# Tool 2: extract_sparameters
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Parse a SPICE .raw AC analysis output and write 2-port S-parameters "
        "to a Touchstone .s2p file."
    ),
)
def extract_sparameters(
    raw_path: Annotated[str, Field(description="Path to .raw file from a previous run.")],
    output_s2p: Annotated[str, Field(description="Path for the output .s2p file.")],
    port_map: Annotated[
        dict[int, str],
        Field(description="Map of port index → SPICE node name (e.g. {1: 'p1', 2: 'p2'})."),
    ],
    z0: Annotated[float, Field(gt=0)] = 50.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        net = extract_sparams_from_raw(raw_path, port_map=port_map, z0=z0)
        out = write_touchstone(net, output_s2p)
        return ok(
            {
                "s2p_path": str(out),
                "n_freq_points": int(net.f.size),
                "freq_range_hz": [float(net.f.min()), float(net.f.max())],
                "z0": z0,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"extract_sparameters failed: {e}", tool_version=__version__)


# ---------------------------------------------------------------------------
# Tool 3: synthesize_lc_filter
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Synthesize an LC ladder LPF (Butterworth / Chebyshev I / elliptic), "
        "write the .asc schematic, and return the component values plus a "
        "Touchstone .s2p of the analytical (lossless ideal) response."
    ),
)
def synthesize_lc_filter(
    filter_type: Annotated[str, Field(description="'butterworth' | 'chebyshev1' | 'elliptic'.")],
    order: Annotated[int, Field(ge=1, le=15)],
    cutoff_hz: Annotated[float, Field(gt=0, description="-3 dB cutoff for Butterworth, ripple edge for Cheby/Ellip.")],
    output_asc: Annotated[str, Field(description="Path for output .asc.")],
    output_s2p: Annotated[str | None, Field(description="Optional path for analytical .s2p preview.")] = None,
    ripple_db: Annotated[float, Field(gt=0, le=3)] = 0.1,
    stopband_atten_db: Annotated[float, Field(gt=0)] = 40.0,
    z0: Annotated[float, Field(gt=0)] = 50.0,
    topology: Annotated[str, Field(description="'series_first' (T) or 'shunt_first' (Pi).")] = "series_first",
    f_sweep_start_hz: Annotated[float, Field(gt=0)] = 1e6,
    f_sweep_stop_hz: Annotated[float, Field(gt=0)] = 5e9,
    f_sweep_npoints: Annotated[int, Field(gt=0, le=10000)] = 801,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        design = synthesize_lc_lpf(
            filter_type,  # type: ignore[arg-type]
            order, cutoff_hz,
            ripple_db=ripple_db, stopband_atten_db=stopband_atten_db,
            z0=z0, topology=Topology(topology),
        )
        is_elliptic = filter_type == "elliptic"
        asc = generate_lpf_asc(
            design.components, output_asc,
            topology="lpf_t_elliptic" if is_elliptic else "lpf_t_butterworth_chebyshev",
            z0=z0,
            f_start_hz=f_sweep_start_hz, f_stop_hz=f_sweep_stop_hz,
        )
        result: dict[str, Any] = {
            "asc_path": str(asc),
            "components": design.components,
            "g_coefficients": design.g,
            "transmission_zeros_hz": design.transmission_zeros_hz,
            "topology": design.topology.value,
            "z0": z0,
            "metadata": design.metadata,
        }
        if output_s2p is not None:
            f = np.geomspace(f_sweep_start_hz, f_sweep_stop_hz, f_sweep_npoints)
            elements = components_dict_to_elements(
                design.components, topology=topology, transmission_zeros=is_elliptic,
            )
            s = ladder_sparams_from_components(elements, f, z0=z0)
            s2p = network_to_touchstone(f, s, output_s2p, z0=z0)
            result["s2p_path"] = str(s2p)
        return ok(result, runtime_sec=timer.elapsed(), tool_version=__version__)
    except Exception as e:
        return error(f"synthesize_lc_filter failed: {e}", tool_version=__version__)


# ---------------------------------------------------------------------------
# Tool 4: place_transmission_zero
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Move the transmission zero of a shunt LC trap to a target frequency, "
        "preserving (by default) the L/C ratio for impedance match. Snaps to "
        "E24 / E96 component series. Updates the .asc in-place."
    ),
)
def place_transmission_zero(
    asc_path: Annotated[str, Field(description="Path to the .asc to edit.")],
    trap_index: Annotated[int, Field(ge=2, description="Trap refdes index (e.g. 2 for L2/C2).")],
    target_freq_hz: Annotated[float, Field(gt=0)],
    preserve_ratio: bool = True,
    snap_series: Annotated[str | None, Field(description="'E24' | 'E96' | 'E192' | None.")] = "E24",
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        comps = read_components(asc_path)
        result = _place_transmission_zero(
            comps, trap_index=trap_index, target_freq_hz=target_freq_hz,
            preserve_ratio=preserve_ratio,
            snap_series=snap_series,  # type: ignore[arg-type]
        )
        new_comps = result["components"]
        l_key = f"L{trap_index}"
        c_key = f"C{trap_index}"
        update_component(asc_path, l_key, new_comps[l_key])
        update_component(asc_path, c_key, new_comps[c_key])
        return ok(
            {
                "trap_index": trap_index,
                "target_freq_hz": target_freq_hz,
                "achieved_freq_hz": result["achieved_freq_hz"],
                "freq_error_pct": result["freq_error_pct"],
                "previous": result["previous"],
                "new": result["new"],
                "asc_path": str(Path(asc_path).resolve()),
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"place_transmission_zero failed: {e}", tool_version=__version__)


# ---------------------------------------------------------------------------
# Tool 7: evaluate_filter_spec
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Evaluate a Touchstone .s2p file against a coex-aware spec. Returns "
        "pass/fail per criterion with margin in dB. Spec format: "
        "{passband: {f_start, f_stop, il_max_db, rl_min_db}, "
        "stopband_targets: [{freq, rejection_min_db, label}, ...]}."
    ),
)
def evaluate_filter_spec_tool(
    s2p_path: Annotated[str, Field(description="Path to Touchstone .s2p.")],
    spec: Annotated[dict, Field(description="Spec dict (see tool description).")],
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        result = evaluate_filter_spec(s2p_path, FilterSpec.model_validate(spec))
        return ok(
            result.model_dump(),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"evaluate_filter_spec failed: {e}", tool_version=__version__)


# ---------------------------------------------------------------------------
# Tool 10: render_response
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Render an S₂ₚ Bode plot as PNG. Optional vertical marker lines for "
        "annotating frequencies of interest (band edges, 2nd / 3rd harmonics)."
    ),
)
def render_response(
    s2p_path: Annotated[str, Field(description="Path to .s2p file.")],
    output_png: Annotated[str, Field(description="Path for output PNG.")],
    freq_range_hz: Annotated[
        list[float] | None,
        Field(description="Optional [f_min, f_max] window in Hz."),
    ] = None,
    markers: Annotated[
        list[list] | None,
        Field(description="List of [freq_hz, label] pairs for vertical guides."),
    ] = None,
    title: Annotated[str | None, Field(description="Plot title (default: filename).")] = None,
    show_s11: bool = True,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        marker_tuples = (
            [(float(f), str(label)) for f, label in markers] if markers else None
        )
        fr = tuple(freq_range_hz) if freq_range_hz else None  # type: ignore[assignment]
        out = _render_response(
            s2p_path, output_png,
            freq_range=fr, markers=marker_tuples, title=title, show_s11=show_s11,
        )
        return ok(
            {"png_path": str(out), "size_bytes": out.stat().st_size},
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"render_response failed: {e}", tool_version=__version__)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server on stdio."""
    log.info("starting mcp-ltspice", extra={"version": __version__})
    mcp.run()


if __name__ == "__main__":
    main()
