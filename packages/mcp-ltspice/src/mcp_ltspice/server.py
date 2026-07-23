"""FastMCP server entry point for mcp-ltspice."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import numpy as np
from fastmcp import FastMCP
from pydantic import Field

from mcp_ltspice import __version__

# Phase 7 modules
from mcp_ltspice.analog import (
    cascaded_lpf_design as _cascaded_lpf,
)
from mcp_ltspice.analog import (
    mfb_band_pass as _mfb_bpf,
)
from mcp_ltspice.analog import (
    mfb_low_pass as _mfb_lpf,
)
from mcp_ltspice.analog import (
    sallen_key_band_pass as _sk_bpf,
)
from mcp_ltspice.analog import (
    sallen_key_high_pass as _sk_hpf,
)
from mcp_ltspice.analog import (
    sallen_key_low_pass as _sk_lpf,
)
from mcp_ltspice.asc_io import (
    generate_lpf_asc,
    read_components,
    update_component,
)
from mcp_ltspice.coex_loop import (
    synthesize_for_coex_target as _synthesize_for_coex_target,
)
from mcp_ltspice.compare import compare_filter_orders as _compare_orders
from mcp_ltspice.digital import (
    DigitalAggressor,
    TimingPath,
)
from mcp_ltspice.digital import (
    check_setup_hold as _check_setup_hold,
)
from mcp_ltspice.digital import (
    estimate_digital_to_analog_crosstalk as _digital_xtalk,
)
from mcp_ltspice.digital import (
    estimate_supply_noise_injection as _supply_noise,
)
from mcp_ltspice.digital import (
    propagation_delay as _prop_delay,
)
from mcp_ltspice.eval import FilterSpec, evaluate_filter_spec
from mcp_ltspice.extract import (
    components_dict_to_elements,
    extract_sparams_from_raw,
    ladder_sparams_from_components,
)
from mcp_ltspice.find_zeros import find_transmission_zeros as _find_zeros
from mcp_ltspice.montecarlo import monte_carlo_analysis as _monte_carlo
from mcp_ltspice.optimize import optimize_filter as _optimize
from mcp_ltspice.power import (
    analyze_ldo as _analyze_ldo,
)
from mcp_ltspice.power import (
    compute_phase_margin as _phase_margin,
)
from mcp_ltspice.power import (
    design_boost as _design_boost,
)
from mcp_ltspice.power import (
    design_buck as _design_buck,
)
from mcp_ltspice.power import (
    type2_compensator as _type2_comp,
)
from mcp_ltspice.power.emc import (
    design_cm_choke as _design_cm_choke,
)
from mcp_ltspice.power.emc import (
    design_dm_input_filter as _design_dm_input_filter,
)
from mcp_ltspice.power.emc import (
    design_pi_output_filter as _design_pi_output_filter,
)
from mcp_ltspice.power.emc import (
    design_rc_snubber as _design_rc_snubber,
)
from mcp_ltspice.power.emc import (
    predict_conducted_emissions as _predict_conducted_emissions,
)
from mcp_ltspice.power.ldo import required_psrr_for_ripple_target as _required_psrr
from mcp_ltspice.render import render_response as _render_response
from mcp_ltspice.report_pdf import build_design_report_pdf as _build_design_report_pdf
from mcp_ltspice.runner import RunResult, Simulator
from mcp_ltspice.runner import run_simulation as _run_simulation
from mcp_ltspice.schematic_render import (
    render_asc_as_schematic as _render_asc_schematic,
)
from mcp_ltspice.schematic_render import (
    render_lc_ladder_schematic as _render_lc_schematic,
)
from mcp_ltspice.srf_check import srf_audit as _srf_audit
from mcp_ltspice.stability import stability_check as _stability_check
from mcp_ltspice.sweep import (
    corner_analysis as _corner_analysis,
)
from mcp_ltspice.sweep import (
    parameter_sweep as _parameter_sweep,
)
from mcp_ltspice.sweep import (
    sensitivity_analysis as _sensitivity,
)
from mcp_ltspice.synthesis import (
    Topology,
    synthesize_lc_bpf,
    synthesize_lc_bsf,
    synthesize_lc_hpf,
    synthesize_lc_lpf,
)
from mcp_ltspice.synthesis import (
    place_transmission_zero as _place_transmission_zero,
)
from mcp_ltspice.validate import result_to_payload as _validation_payload
from mcp_ltspice.validate import validate_against_spice as _validate_against_spice
from mcp_ltspice.vendor_fetch import register_user_vendor_dir as _register_user_vendor_dir
from mcp_ltspice.vendor_models import (
    list_vendor_parts as _list_vendor_parts,
)
from mcp_ltspice.vendor_models import (
    substitute_real_components as _substitute_real,
)
from mcp_ltspice.vendors import (
    find_mosfet_for_application as _find_mosfet,
)
from mcp_ltspice.vendors import (
    find_opamp_for_application as _find_opamp,
)
from mcp_ltspice.vendors import (
    list_bjts as _list_bjts,
)
from mcp_ltspice.vendors import (
    list_diodes as _list_diodes,
)
from mcp_ltspice.vendors import (
    list_mosfets as _list_mosfets,
)
from mcp_ltspice.vendors import (
    list_opamps as _list_opamps,
)
from mcp_ltspice.vendors import (
    list_references as _list_refs,
)
from mcp_ltspice.vendors import (
    lookup_bjt as _lookup_bjt,
)
from mcp_ltspice.vendors import (
    lookup_diode as _lookup_diode,
)
from mcp_ltspice.vendors import (
    lookup_mosfet as _lookup_mosfet,
)
from mcp_ltspice.vendors import (
    lookup_opamp as _lookup_opamp,
)
from mcp_ltspice.vendors import (
    lookup_reference as _lookup_ref,
)
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
        result: RunResult = _run_simulation(asc_path, prefer=prefer_enum, timeout=timeout_sec)
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
    except Exception as e:
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
    cutoff_hz: Annotated[
        float, Field(gt=0, description="-3 dB cutoff for Butterworth, ripple edge for Cheby/Ellip.")
    ],
    output_asc: Annotated[str, Field(description="Path for output .asc.")],
    output_s2p: Annotated[
        str | None, Field(description="Optional path for analytical .s2p preview.")
    ] = None,
    ripple_db: Annotated[float, Field(gt=0, le=3)] = 0.1,
    stopband_atten_db: Annotated[float, Field(gt=0)] = 40.0,
    z0: Annotated[float, Field(gt=0)] = 50.0,
    topology: Annotated[
        str, Field(description="'series_first' (T) or 'shunt_first' (Pi).")
    ] = "series_first",
    f_sweep_start_hz: Annotated[float, Field(gt=0)] = 1e6,
    f_sweep_stop_hz: Annotated[float, Field(gt=0)] = 5e9,
    f_sweep_npoints: Annotated[int, Field(gt=0, le=10000)] = 801,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        design = synthesize_lc_lpf(
            filter_type,  # type: ignore[arg-type]
            order,
            cutoff_hz,
            ripple_db=ripple_db,
            stopband_atten_db=stopband_atten_db,
            z0=z0,
            topology=Topology(topology),
        )
        is_elliptic = filter_type == "elliptic"
        asc = generate_lpf_asc(
            design.components,
            output_asc,
            topology="lpf_t_elliptic" if is_elliptic else "lpf_t_butterworth_chebyshev",
            z0=z0,
            f_start_hz=f_sweep_start_hz,
            f_stop_hz=f_sweep_stop_hz,
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
                design.components,
                topology=topology,
                transmission_zeros=is_elliptic,
            )
            s = ladder_sparams_from_components(elements, f, z0=z0)
            s2p = network_to_touchstone(f, s, output_s2p, z0=z0)
            result["s2p_path"] = str(s2p)
        return ok(result, runtime_sec=timer.elapsed(), tool_version=__version__)
    except Exception as e:
        return error(f"synthesize_lc_filter failed: {e}", tool_version=__version__)


@mcp.tool(
    description=(
        "Synthesize a high-pass LC ladder via the LPF→HPF frequency transformation "
        "(Pozar §8.5). Series inductors become series capacitors; shunt capacitors "
        "become shunt inductors. Components emitted as C1, L2, C3, L4, ... (T-topology). "
        "Elliptic (odd order ≥3) inverts the LPF prototype ladder element-by-element, "
        "moving each finite zero to ω_c²/ω_z in the lower stopband."
    ),
)
def synthesize_lc_hpf_filter(
    filter_type: Annotated[
        str, Field(description="'butterworth' | 'chebyshev1' | 'elliptic' (odd order ≥3).")
    ],
    order: Annotated[int, Field(ge=1, le=15)],
    cutoff_hz: Annotated[float, Field(gt=0, description="-3 dB cutoff frequency.")],
    ripple_db: Annotated[float, Field(gt=0, le=3)] = 0.1,
    stopband_atten_db: Annotated[float, Field(gt=0)] = 40.0,
    z0: Annotated[float, Field(gt=0)] = 50.0,
    topology: Annotated[
        str, Field(description="'series_first' (T) or 'shunt_first' (Pi).")
    ] = "series_first",
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        design = synthesize_lc_hpf(
            filter_type,  # type: ignore[arg-type]
            order,
            cutoff_hz,
            ripple_db=ripple_db,
            stopband_atten_db=stopband_atten_db,
            z0=z0,
            topology=Topology(topology),
        )
        return ok(
            {
                "components": design.components,
                "g_coefficients": design.g,
                "transmission_zeros_hz": design.transmission_zeros_hz,
                "topology": design.topology.value,
                "cutoff_hz": design.cutoff_hz,
                "z0": z0,
                "metadata": design.metadata,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"synthesize_lc_hpf_filter failed: {e}", tool_version=__version__)


@mcp.tool(
    description=(
        "Synthesize a band-pass LC ladder via the LPF→BPF frequency transformation "
        "(Pozar §8.5). Series inductors become series-LC tanks (resonant at f₀); "
        "shunt capacitors become shunt-LC tanks (parallel-resonant). Component count "
        "doubles vs. the LPF prototype. f₀ = √(f_low · f_high) (geometric mean); "
        "fractional bandwidth Δ = (f_high - f_low) / f₀. Components emitted with "
        "'_s' suffix on series-LC pairs. Elliptic (odd order ≥3) maps each LPF trap "
        "to a four-element composite shunt branch {Lk_s, Ck_s, Lk, Ck} whose two "
        "resonances are the images ω₀(√(b²+1) ± b), b = ω_z·Δ/2, of the prototype "
        "zero — notch pairs straddling the passband."
    ),
)
def synthesize_lc_bpf_filter(
    filter_type: Annotated[
        str, Field(description="'butterworth' | 'chebyshev1' | 'elliptic' (odd order ≥3).")
    ],
    order: Annotated[int, Field(ge=1, le=15)],
    f_low_hz: Annotated[float, Field(gt=0, description="Lower band edge.")],
    f_high_hz: Annotated[float, Field(gt=0, description="Upper band edge.")],
    ripple_db: Annotated[float, Field(gt=0, le=3)] = 0.1,
    stopband_atten_db: Annotated[float, Field(gt=0)] = 40.0,
    z0: Annotated[float, Field(gt=0)] = 50.0,
    topology: Annotated[
        str, Field(description="'series_first' or 'shunt_first'.")
    ] = "series_first",
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        design = synthesize_lc_bpf(
            filter_type,  # type: ignore[arg-type]
            order,
            f_low_hz,
            f_high_hz,
            ripple_db=ripple_db,
            stopband_atten_db=stopband_atten_db,
            z0=z0,
            topology=Topology(topology),
        )
        return ok(
            {
                "components": design.components,
                "g_coefficients": design.g,
                "transmission_zeros_hz": design.transmission_zeros_hz,
                "topology": design.topology.value,
                "f_0_hz": design.metadata["f_0_hz"],
                "f_low_hz": design.metadata["f_low_hz"],
                "f_high_hz": design.metadata["f_high_hz"],
                "fractional_bandwidth": design.metadata["fractional_bandwidth"],
                "z0": z0,
                "metadata": design.metadata,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"synthesize_lc_bpf_filter failed: {e}", tool_version=__version__)


@mcp.tool(
    description=(
        "Synthesize a band-stop LC ladder via the LPF→BSF frequency transformation "
        "(Pozar §8.5). Series inductors become parallel-LC anti-resonators (open at f₀); "
        "shunt capacitors become series-LC resonators (short at f₀). Used to notch out "
        "a specific band (e.g., LO leakage, image rejection). Elliptic (odd order ≥3) "
        "maps each LPF trap to a four-element composite shunt branch {Lk_s, Ck_s, Lk, Ck} "
        "with zero pairs ω₀(√(b²+1) ± b), b = Δ/(2ω_z), inside the notch."
    ),
)
def synthesize_lc_bsf_filter(
    filter_type: Annotated[
        str, Field(description="'butterworth' | 'chebyshev1' | 'elliptic' (odd order ≥3).")
    ],
    order: Annotated[int, Field(ge=1, le=15)],
    f_low_hz: Annotated[float, Field(gt=0, description="Lower stopband edge.")],
    f_high_hz: Annotated[float, Field(gt=0, description="Upper stopband edge.")],
    ripple_db: Annotated[float, Field(gt=0, le=3)] = 0.1,
    stopband_atten_db: Annotated[float, Field(gt=0)] = 40.0,
    z0: Annotated[float, Field(gt=0)] = 50.0,
    topology: Annotated[
        str, Field(description="'series_first' or 'shunt_first'.")
    ] = "series_first",
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        design = synthesize_lc_bsf(
            filter_type,  # type: ignore[arg-type]
            order,
            f_low_hz,
            f_high_hz,
            ripple_db=ripple_db,
            stopband_atten_db=stopband_atten_db,
            z0=z0,
            topology=Topology(topology),
        )
        return ok(
            {
                "components": design.components,
                "g_coefficients": design.g,
                "transmission_zeros_hz": design.transmission_zeros_hz,
                "topology": design.topology.value,
                "f_0_hz": design.metadata["f_0_hz"],
                "f_low_hz": design.metadata["f_low_hz"],
                "f_high_hz": design.metadata["f_high_hz"],
                "fractional_bandwidth": design.metadata["fractional_bandwidth"],
                "z0": z0,
                "metadata": design.metadata,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"synthesize_lc_bsf_filter failed: {e}", tool_version=__version__)


@mcp.tool(
    description=(
        "Closed-loop coex-driven synthesis: iterate elliptic LPF order until "
        "the coexistence matrix meets a desense target. Each iteration places "
        "transmission zeros on the victim-weighted harmonic centroids "
        "(place_zeros_for_coex), aims the traps, substitutes real vendor "
        "parts (SRF-checked with graceful margin fallback 1.2→1.0→off, "
        "reported per iteration), evaluates the realized ladder's rejection "
        "analytically, and runs the GNSS-aware coex matrix. Victim entries "
        "are coex-matrix RX dicts; victim_type='gnss' gets the realized "
        "filter's rejection at its frequency injected automatically. Returns "
        "converged flag, chosen order, realized components, the zero plan, "
        "the final matrix, and the full iteration log (best-so-far when not "
        "converged)."
    ),
)
def synthesize_for_coex_target(
    passband_hz: Annotated[
        list[float], Field(description="[f_low_hz, f_high_hz] of the TX passband.")
    ],
    pa_power_dbm: float,
    victim_bands: list[dict[str, Any]],
    target_max_desense_db: float = 0.0,
    antenna_iso_db: Annotated[float, Field(ge=0)] = 25.0,
    min_order: Annotated[int, Field(ge=3, le=15)] = 5,
    max_order: Annotated[int, Field(ge=3, le=15)] = 11,
    inductor_vendor: str = "coilcraft_0402hp",
    capacitor_vendor: str = "murata_gjm_c0g",
    ripple_db: Annotated[float, Field(gt=0, le=3)] = 0.1,
    stopband_atten_db: Annotated[float, Field(gt=0)] = 50.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        result = _synthesize_for_coex_target(
            (passband_hz[0], passband_hz[1]),
            pa_power_dbm,
            victim_bands,
            target_max_desense_db=target_max_desense_db,
            antenna_iso_db=antenna_iso_db,
            min_order=min_order,
            max_order=max_order,
            inductor_vendor=inductor_vendor,
            capacitor_vendor=capacitor_vendor,
            ripple_db=ripple_db,
            stopband_atten_db=stopband_atten_db,
        )
        return ok(result, runtime_sec=timer.elapsed(), tool_version=__version__)
    except Exception as e:
        return error(f"synthesize_for_coex_target failed: {e}", tool_version=__version__)


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
            comps,
            trap_index=trap_index,
            target_freq_hz=target_freq_hz,
            preserve_ratio=preserve_ratio,
            snap_series=snap_series,
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
    spec: Annotated[dict[str, Any], Field(description="Spec dict (see tool description).")],
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
        list[list[Any]] | None,
        Field(description="List of [freq_hz, label] pairs for vertical guides."),
    ] = None,
    title: Annotated[str | None, Field(description="Plot title (default: filename).")] = None,
    show_s11: bool = True,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        marker_tuples = [(float(f), str(label)) for f, label in markers] if markers else None
        fr: tuple[float, float] | None = None
        if freq_range_hz:
            if len(freq_range_hz) != 2:
                raise ValueError(
                    f"freq_range_hz must be [f_min, f_max]; got {len(freq_range_hz)} value(s)"
                )
            fr = (float(freq_range_hz[0]), float(freq_range_hz[1]))
        out = _render_response(
            s2p_path,
            output_png,
            freq_range=fr,
            markers=marker_tuples,
            title=title,
            show_s11=show_s11,
        )
        return ok(
            {"png_path": str(out), "size_bytes": out.stat().st_size},
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"render_response failed: {e}", tool_version=__version__)


# ---------------------------------------------------------------------------
# Tool 5: find_transmission_zeros
# ---------------------------------------------------------------------------


@mcp.tool(
    description="Locate notches (transmission zeros) in S21 by peak detection.",
)
def find_transmission_zeros(
    s2p_path: Annotated[str, Field(description="Path to .s2p file.")],
    min_depth_db: Annotated[float, Field(gt=0)] = 20.0,
    f_min_hz: float | None = None,
    f_max_hz: float | None = None,
) -> Envelope[list[dict[str, float]]]:
    timer = Timer()
    try:
        return ok(
            _find_zeros(
                s2p_path,
                min_depth_db=min_depth_db,
                f_min_hz=f_min_hz,
                f_max_hz=f_max_hz,
            ),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"find_transmission_zeros failed: {e}", tool_version=__version__)


# ---------------------------------------------------------------------------
# Tool 6: substitute_real_components
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Replace ideal L/C values with vendor parts. Returns parasitic data "
        "(Cp/Ls, ESR, SRF) so downstream sims include realistic loss behavior. "
        "Vendors: 'coilcraft_0402hp', 'coilcraft_0603cs', 'murata_gjm_c0g', "
        "'johanson_l' (L-07W), 'tdk_mlg' (MLK1005S). Set srf_margin > 0 (e.g. 1.2) "
        "to auto-reject parts whose SRF < srf_margin × max_spec_freq_hz; "
        "provide either max_spec_freq_hz directly or a spec dict from which it's derived."
    ),
)
def substitute_real_components(
    components: dict[str, float],
    inductor_vendor: str = "coilcraft_0402hp",
    capacitor_vendor: str = "murata_gjm_c0g",
    srf_margin: Annotated[float, Field(ge=0)] = 0.0,
    max_spec_freq_hz: Annotated[float | None, Field(gt=0)] = None,
    spec: dict[str, Any] | None = None,
    max_value_drift_pct: Annotated[float | None, Field(gt=0)] = 25.0,
) -> Envelope[dict[str, dict[str, Any]]]:
    timer = Timer()
    try:
        return ok(
            _substitute_real(
                components,
                inductor_vendor,
                capacitor_vendor,
                srf_margin=srf_margin,
                max_spec_freq_hz=max_spec_freq_hz,
                spec=spec,
                max_value_drift_pct=max_value_drift_pct,
            ),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"substitute_real_components failed: {e}", tool_version=__version__)


@mcp.tool(description="List the value catalogue for a vendor part series.")
def list_vendor_parts(vendor: str) -> Envelope[list[float]]:
    timer = Timer()
    try:
        return ok(
            _list_vendor_parts(vendor),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"list_vendor_parts failed: {e}", tool_version=__version__)


# ---------------------------------------------------------------------------
# Tool 8: optimize_filter
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Iteratively tune component values against a spec via Nelder-Mead. "
        "Loss = sum of negative spec margins (failing criteria only). Final "
        "values snapped to E24 / E96 by default."
    ),
)
def optimize_filter(
    initial_components: dict[str, float],
    spec: dict[str, Any],
    tune: list[str] | None = None,
    transmission_zeros: bool = True,
    z0: Annotated[float, Field(gt=0)] = 50.0,
    method: str = "Nelder-Mead",
    max_iter: Annotated[int, Field(gt=0, le=5000)] = 500,
    snap_series: str | None = "E24",
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        result = _optimize(
            initial_components,
            spec,
            tune=tune,
            transmission_zeros=transmission_zeros,
            z0=z0,
            method=method,  # type: ignore[arg-type]
            max_iter=max_iter,
            snap_series=snap_series,
        )
        return ok(
            {
                "initial_components": result.initial_components,
                "optimized_components": result.optimized_components,
                "snapped_components": result.snapped_components,
                "initial_loss": result.initial_loss,
                "final_loss": result.final_loss,
                "n_iterations": result.n_iterations,
                "converged": result.converged,
                "margins_initial": result.margins_initial,
                "margins_final": result.margins_final,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"optimize_filter failed: {e}", tool_version=__version__)


# ---------------------------------------------------------------------------
# Tool 9: monte_carlo_analysis
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Run Monte Carlo trials with Gaussian-distributed component tolerances. "
        "Reports yield (% passing the spec) and per-metric mean/std/percentiles. "
        "Set trace=True to also emit a JSONL file with one record per trial "
        "(seed, components, metrics, passed, failures) for root-cause analysis "
        "of yield loss."
    ),
)
def monte_carlo_analysis(
    components: dict[str, float],
    spec: dict[str, Any],
    tolerance_pct: dict[str, float] | float = 5.0,
    n_runs: Annotated[int, Field(gt=0, le=100000)] = 1000,
    z0: Annotated[float, Field(gt=0)] = 50.0,
    transmission_zeros: bool = True,
    n_jobs: int = -1,
    trace: bool = False,
    trace_path: str | None = None,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        result = _monte_carlo(
            components,
            spec,
            tolerance_pct=tolerance_pct,
            n_runs=n_runs,
            z0=z0,
            transmission_zeros=transmission_zeros,
            n_jobs=n_jobs,
            trace=trace,
            trace_path=trace_path,
        )
        return ok(
            {
                "n_runs": result.n_runs,
                "n_passing": result.n_passing,
                "yield_pct": result.yield_pct,
                "per_metric_stats": result.per_metric_stats,
                "failing_criteria_counts": result.failing_criteria_counts,
                "trace_path": result.trace_path,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"monte_carlo_analysis failed: {e}", tool_version=__version__)


# ---------------------------------------------------------------------------
# Tool 11: stability_check
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Compute Rollett K-factor, |Δ|, and Edwards-Sinsky μ-factor across "
        "frequency for a 2-port network. Use for amplifier/oscillator stability."
    ),
)
def stability_check(
    s2p_path: Annotated[str, Field(description="Path to .s2p file.")],
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        return ok(
            _stability_check(s2p_path),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"stability_check failed: {e}", tool_version=__version__)


# ===========================================================================
# Phase 7 tools: sweep / SRF / analog / power / digital / vendor catalogs
# ===========================================================================


def _wrap(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Envelope[Any]:
    """Run a callable inside the standard envelope contract."""
    timer = Timer()
    try:
        return ok(
            func(*args, **kwargs),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"{func.__name__} failed: {e}", tool_version=__version__)


# ----- DOE / sweep ---------------------------------------------------------


@mcp.tool(
    description=(
        "Sweep one or more component values across a Cartesian product grid "
        "and report per-point spec margins + overall yield."
    ),
)
def parameter_sweep(
    components: dict[str, float],
    sweep: dict[str, list[float]],
    spec: dict[str, Any],
    z0: Annotated[float, Field(gt=0)] = 50.0,
    transmission_zeros: bool = True,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        result = _parameter_sweep(
            components,
            sweep,
            spec,
            z0=z0,
            transmission_zeros=transmission_zeros,
        )
        return ok(
            {
                "n_points": result.n_points,
                "n_passing": result.n_passing,
                "yield_pct": result.yield_pct,
                "points": [
                    {
                        "parameters": p.parameters,
                        "margins": p.margins,
                        "overall": p.overall,
                    }
                    for p in result.points
                ],
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"parameter_sweep failed: {e}", tool_version=__version__)


@mcp.tool(
    description=(
        "Evaluate a filter spec at named corners (e.g. TT/SS/FF or "
        "application-specific stress combinations). Each corner is a dict "
        "of refdes -> multiplier."
    ),
)
def corner_analysis(
    components: dict[str, float],
    corners: dict[str, dict[str, float]],
    spec: dict[str, Any],
    z0: Annotated[float, Field(gt=0)] = 50.0,
    transmission_zeros: bool = True,
) -> Envelope[dict[str, Any]]:
    return _wrap(
        _corner_analysis,
        components,
        corners,
        spec,
        z0=z0,
        transmission_zeros=transmission_zeros,
    )


@mcp.tool(
    description=(
        "Perturb each component by +/-pct and report the dB/% sensitivity of "
        "every spec criterion. Ranks components by total influence so you "
        "know which ones to grade tightly."
    ),
)
def sensitivity_analysis(
    components: dict[str, float],
    spec: dict[str, Any],
    perturbation_pct: Annotated[float, Field(gt=0, le=10)] = 1.0,
    z0: Annotated[float, Field(gt=0)] = 50.0,
    transmission_zeros: bool = True,
) -> Envelope[dict[str, Any]]:
    return _wrap(
        _sensitivity,
        components,
        spec,
        perturbation_pct=perturbation_pct,
        z0=z0,
        transmission_zeros=transmission_zeros,
    )


# ----- SRF audit -----------------------------------------------------------


@mcp.tool(
    description=(
        "Flag inductors / capacitors whose self-resonant frequency is within "
        "margin_pct of the highest spec target. Above SRF the analytical "
        "model isn't predictive of real measurement."
    ),
)
def srf_audit(
    components: dict[str, float],
    spec: dict[str, Any],
    inductor_vendor: str = "coilcraft_0402hp",
    capacitor_vendor: str = "murata_gjm_c0g",
    margin_pct: Annotated[float, Field(gt=0, le=100)] = 30.0,
) -> Envelope[dict[str, Any]]:
    return _wrap(
        _srf_audit,
        components,
        spec,
        inductor_vendor=inductor_vendor,
        capacitor_vendor=capacitor_vendor,
        margin_pct=margin_pct,
    )


# ----- Analog active filters ----------------------------------------------


@mcp.tool(description="Synthesize a Sallen-Key 2nd-order LPF (op-amp + 2R + 2C).")
def sallen_key_low_pass(
    fc_hz: Annotated[float, Field(gt=0)],
    q: Annotated[float, Field(gt=0)] = 0.7071,
    gain_v_v: Annotated[float, Field(gt=0)] = 1.0,
    c_pf: Annotated[float, Field(gt=0)] = 1000.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        d = _sk_lpf(fc_hz, q=q, gain_v_v=gain_v_v, c_pf=c_pf)
        return ok(
            {
                "topology": d.topology,
                "fc_hz": d.fc_hz,
                "q": d.q,
                "gain_v_v": d.gain_v_v,
                "R1": d.R1,
                "R2": d.R2,
                "R3": d.R3,
                "R4": d.R4,
                "C1": d.C1,
                "C2": d.C2,
                "op_amp_min_gbw_hz": d.op_amp_min_gbw_hz,
                "notes": d.notes,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"sallen_key_low_pass failed: {e}", tool_version=__version__)


@mcp.tool(description="Synthesize a Sallen-Key 2nd-order HPF.")
def sallen_key_high_pass(
    fc_hz: Annotated[float, Field(gt=0)],
    q: Annotated[float, Field(gt=0)] = 0.7071,
    r_kohm: Annotated[float, Field(gt=0)] = 10.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        d = _sk_hpf(fc_hz, q=q, r_kohm=r_kohm)
        return ok(
            {
                "topology": d.topology,
                "fc_hz": d.fc_hz,
                "q": d.q,
                "R1": d.R1,
                "R2": d.R2,
                "C1": d.C1,
                "C2": d.C2,
                "op_amp_min_gbw_hz": d.op_amp_min_gbw_hz,
                "notes": d.notes,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"sallen_key_high_pass failed: {e}", tool_version=__version__)


@mcp.tool(description="Synthesize a Sallen-Key 2nd-order BPF (single op-amp).")
def sallen_key_band_pass(
    fc_hz: Annotated[float, Field(gt=0)],
    q: Annotated[float, Field(gt=0)] = 1.0,
    r_kohm: Annotated[float, Field(gt=0)] = 10.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        d = _sk_bpf(fc_hz, q=q, r_kohm=r_kohm)
        return ok(
            {
                "topology": d.topology,
                "fc_hz": d.fc_hz,
                "q": d.q,
                "gain_v_v": d.gain_v_v,
                "R1": d.R1,
                "R2": d.R2,
                "R3": d.R3,
                "R4": d.R4,
                "C1": d.C1,
                "C2": d.C2,
                "op_amp_min_gbw_hz": d.op_amp_min_gbw_hz,
                "notes": d.notes,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"sallen_key_band_pass failed: {e}", tool_version=__version__)


@mcp.tool(description="Synthesize a Multiple-Feedback (MFB) 2nd-order LPF.")
def mfb_low_pass(
    fc_hz: Annotated[float, Field(gt=0)],
    q: Annotated[float, Field(gt=0)] = 0.7071,
    gain_v_v: Annotated[float, Field(gt=0)] = 1.0,
    c_pf: Annotated[float, Field(gt=0)] = 1000.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        d = _mfb_lpf(fc_hz, q=q, gain_v_v=gain_v_v, c_pf=c_pf)
        return ok(
            {
                "topology": d.topology,
                "fc_hz": d.fc_hz,
                "q": d.q,
                "gain_v_v": d.gain_v_v,
                "R1": d.R1,
                "R2": d.R2,
                "R3": d.R3,
                "C1": d.C1,
                "C2": d.C2,
                "op_amp_min_gbw_hz": d.op_amp_min_gbw_hz,
                "notes": d.notes,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"mfb_low_pass failed: {e}", tool_version=__version__)


@mcp.tool(description="Synthesize a Multiple-Feedback (MFB) 2nd-order BPF.")
def mfb_band_pass(
    fc_hz: Annotated[float, Field(gt=0)],
    q: Annotated[float, Field(gt=0)] = 5.0,
    gain_v_v: Annotated[float, Field(gt=0)] = 1.0,
    c_pf: Annotated[float, Field(gt=0)] = 100.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        d = _mfb_bpf(fc_hz, q=q, gain_v_v=gain_v_v, c_pf=c_pf)
        return ok(
            {
                "topology": d.topology,
                "fc_hz": d.fc_hz,
                "q": d.q,
                "gain_v_v": d.gain_v_v,
                "R1": d.R1,
                "R2": d.R2,
                "R3": d.R3,
                "C1": d.C1,
                "C2": d.C2,
                "op_amp_min_gbw_hz": d.op_amp_min_gbw_hz,
                "notes": d.notes,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"mfb_band_pass failed: {e}", tool_version=__version__)


@mcp.tool(
    description=(
        "Cascaded nth-order Butterworth or Bessel LPF as 2nd-order stages "
        "(Sallen-Key). Returns per-stage component values + required op-amp GBW."
    ),
)
def cascaded_lpf_design(
    fc_hz: Annotated[float, Field(gt=0)],
    order: Annotated[int, Field(ge=2, le=8)],
    response: str = "butterworth",
    c_pf: Annotated[float, Field(gt=0)] = 1000.0,
) -> Envelope[dict[str, Any]]:
    return _wrap(_cascaded_lpf, fc_hz, order, response=response, c_pf=c_pf)


# ----- Power supply tools --------------------------------------------------


@mcp.tool(
    description="Analyze an LDO at one operating point: efficiency, dropout, dissipation, output ripple."
)
def analyze_ldo(
    v_in_v: Annotated[float, Field(gt=0)],
    v_out_v: Annotated[float, Field(gt=0)],
    i_out_a: Annotated[float, Field(ge=0)],
    dropout_v: Annotated[float, Field(gt=0)] = 0.3,
    psrr_db: Annotated[float, Field(gt=0)] = 60.0,
    v_ripple_in_mvpp: float | None = None,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        r = _analyze_ldo(
            v_in_v=v_in_v,
            v_out_v=v_out_v,
            i_out_a=i_out_a,
            dropout_v=dropout_v,
            psrr_db=psrr_db,
            v_ripple_in_mvpp=v_ripple_in_mvpp,
        )
        return ok(
            {
                "v_in_v": r.v_in_v,
                "v_out_v": r.v_out_v,
                "i_out_a": r.i_out_a,
                "headroom_v": r.headroom_v,
                "dissipation_w": r.dissipation_w,
                "efficiency_pct": r.efficiency_pct,
                "output_ripple_uvpp": r.output_ripple_uvpp,
                "notes": r.notes,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"analyze_ldo failed: {e}", tool_version=__version__)


@mcp.tool(description="Compute the PSRR (dB) an LDO needs to meet an output-ripple target.")
def required_psrr_for_ripple(
    v_ripple_in_mvpp: Annotated[float, Field(gt=0)],
    v_ripple_out_uvpp_max: Annotated[float, Field(gt=0)],
) -> Envelope[dict[str, float]]:
    timer = Timer()
    try:
        psrr = _required_psrr(
            v_ripple_in_mvpp=v_ripple_in_mvpp,
            v_ripple_out_uvpp_max=v_ripple_out_uvpp_max,
        )
        return ok(
            {"required_psrr_db": psrr},
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"required_psrr_for_ripple failed: {e}", tool_version=__version__)


@mcp.tool(description="Size a Buck (step-down) SMPS: L, Cout, ESR limit, peak/RMS currents.")
def design_buck(
    v_in_v: Annotated[float, Field(gt=0)],
    v_out_v: Annotated[float, Field(gt=0)],
    i_out_a: Annotated[float, Field(gt=0)],
    f_sw_hz: Annotated[float, Field(gt=0)] = 1e6,
    inductor_ripple_pct: Annotated[float, Field(gt=0, le=100)] = 30.0,
    output_ripple_mvpp: Annotated[float, Field(gt=0)] = 20.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        d = _design_buck(
            v_in_v=v_in_v,
            v_out_v=v_out_v,
            i_out_a=i_out_a,
            f_sw_hz=f_sw_hz,
            inductor_ripple_pct=inductor_ripple_pct,
            output_ripple_mvpp=output_ripple_mvpp,
        )
        return ok(
            {
                "v_in_v": d.v_in_v,
                "v_out_v": d.v_out_v,
                "i_out_a": d.i_out_a,
                "f_sw_hz": d.f_sw_hz,
                "duty_cycle": d.duty_cycle,
                "L_h": d.L_h,
                "Cout_f": d.Cout_f,
                "Cout_esr_max_ohm": d.Cout_esr_max_ohm,
                "inductor_peak_a": d.inductor_peak_a,
                "inductor_rms_a": d.inductor_rms_a,
                "expected_efficiency_pct": d.expected_efficiency_pct,
                "notes": d.notes,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"design_buck failed: {e}", tool_version=__version__)


@mcp.tool(description="Size a Boost (step-up) SMPS: L, Cout, ESR limit, peak/RMS currents.")
def design_boost(
    v_in_v: Annotated[float, Field(gt=0)],
    v_out_v: Annotated[float, Field(gt=0)],
    i_out_a: Annotated[float, Field(gt=0)],
    f_sw_hz: Annotated[float, Field(gt=0)] = 500e3,
    inductor_ripple_pct: Annotated[float, Field(gt=0, le=100)] = 30.0,
    output_ripple_mvpp: Annotated[float, Field(gt=0)] = 50.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        d = _design_boost(
            v_in_v=v_in_v,
            v_out_v=v_out_v,
            i_out_a=i_out_a,
            f_sw_hz=f_sw_hz,
            inductor_ripple_pct=inductor_ripple_pct,
            output_ripple_mvpp=output_ripple_mvpp,
        )
        return ok(
            {
                "v_in_v": d.v_in_v,
                "v_out_v": d.v_out_v,
                "i_out_a": d.i_out_a,
                "f_sw_hz": d.f_sw_hz,
                "duty_cycle": d.duty_cycle,
                "L_h": d.L_h,
                "Cout_f": d.Cout_f,
                "Cout_esr_max_ohm": d.Cout_esr_max_ohm,
                "inductor_peak_a": d.inductor_peak_a,
                "inductor_rms_a": d.inductor_rms_a,
                "notes": d.notes,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"design_boost failed: {e}", tool_version=__version__)


@mcp.tool(description="Type-II compensator design (1 zero + 1 pole) for current-mode SMPS loops.")
def type2_compensator(
    crossover_hz: Annotated[float, Field(gt=0)],
    plant_zero_hz: Annotated[float, Field(gt=0)],
    plant_pole_hz: Annotated[float, Field(gt=0)],
    phase_boost_deg: Annotated[float, Field(gt=0, lt=90)] = 60.0,
    rfb_kohm: Annotated[float, Field(gt=0)] = 10.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        d = _type2_comp(
            crossover_hz=crossover_hz,
            plant_zero_hz=plant_zero_hz,
            plant_pole_hz=plant_pole_hz,
            phase_boost_deg=phase_boost_deg,
            rfb_kohm=rfb_kohm,
        )
        return ok(
            {
                "topology": d.topology,
                "crossover_hz": d.crossover_hz,
                "phase_margin_deg": d.phase_margin_deg,
                "components": d.components,
                "transfer_function_hz": d.transfer_function_hz,
                "transfer_function_db": d.transfer_function_db,
                "transfer_function_phase_deg": d.transfer_function_phase_deg,
                "notes": d.notes,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"type2_compensator failed: {e}", tool_version=__version__)


@mcp.tool(description="Compute crossover freq + phase margin from open-loop Bode arrays.")
def compute_phase_margin(
    open_loop_freq_hz: list[float],
    open_loop_mag_db: list[float],
    open_loop_phase_deg: list[float],
) -> Envelope[dict[str, Any]]:
    return _wrap(
        _phase_margin,
        open_loop_freq_hz,
        open_loop_mag_db,
        open_loop_phase_deg,
    )


# ----- Power-supply EMC pre-compliance ------------------------------------


@mcp.tool(
    description=(
        "Size a Pi-section LC output filter (C-L-C) for additional SMPS "
        "ripple attenuation downstream of the converter's built-in Cout. "
        "Returns L, C_in, C_out, predicted attenuation, and damping advice."
    ),
)
def design_pi_output_filter(
    f_switching_hz: Annotated[float, Field(gt=0)],
    attenuation_target_db: Annotated[float, Field(gt=0)] = 40.0,
    f_target_hz: Annotated[float | None, Field(gt=0)] = None,
    i_out_a: Annotated[float, Field(gt=0)] = 1.0,
    c_in_initial_f: Annotated[float, Field(gt=0)] = 10e-6,
    cap_voltage_rating_v: Annotated[float, Field(gt=0)] = 25.0,
    z0_load_ohm: Annotated[float, Field(gt=0)] = 1.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        result = _design_pi_output_filter(
            f_switching_hz=f_switching_hz,
            f_target_hz=f_target_hz,
            attenuation_target_db=attenuation_target_db,
            i_out_a=i_out_a,
            c_in_initial_f=c_in_initial_f,
            cap_voltage_rating_v=cap_voltage_rating_v,
            z0_load_ohm=z0_load_ohm,
        )
        return ok(
            {
                "L_h": result.L_h,
                "C_in_f": result.C_in_f,
                "C_out_f": result.C_out_f,
                "f_resonance_hz": result.f_resonance_hz,
                "attenuation_at_f_target_db": result.attenuation_at_f_target_db,
                "attenuation_at_f_sw_db": result.attenuation_at_f_sw_db,
                "damping_resistor_advice": result.damping_resistor_advice,
                "notes": result.notes,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"design_pi_output_filter failed: {e}", tool_version=__version__)


@mcp.tool(
    description=(
        "Size a 2nd-order LC differential-mode input EMI filter for "
        "conducted-emissions compliance, with the Middlebrook stability check "
        "(|Z_out_filter| < |Z_in_converter| with safety_factor margin) so the "
        "filter doesn't destabilise the converter's loop."
    ),
)
def design_dm_input_filter(
    f_switching_hz: Annotated[float, Field(gt=0)],
    attenuation_target_db: Annotated[float, Field(gt=0)] = 40.0,
    i_in_a: Annotated[float, Field(gt=0)] = 1.0,
    converter_input_impedance_ohm: Annotated[float, Field(gt=0)] = 1.0,
    lisn_impedance_ohm: Annotated[float, Field(gt=0)] = 50.0,
    safety_factor: Annotated[float, Field(gt=1)] = 6.0,
    c_initial_f: Annotated[float, Field(gt=0)] = 4.7e-6,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        result = _design_dm_input_filter(
            f_switching_hz=f_switching_hz,
            attenuation_target_db=attenuation_target_db,
            i_in_a=i_in_a,
            converter_input_impedance_ohm=converter_input_impedance_ohm,
            lisn_impedance_ohm=lisn_impedance_ohm,
            safety_factor=safety_factor,
            c_initial_f=c_initial_f,
        )
        return ok(
            {
                "L_h": result.L_h,
                "C_f": result.C_f,
                "f_corner_hz": result.f_corner_hz,
                "attenuation_at_f_sw_db": result.attenuation_at_f_sw_db,
                "damping_resistor_ohm": result.damping_resistor_ohm,
                "damping_cap_f": result.damping_cap_f,
                "middlebrook_margin_db": result.middlebrook_margin_db,
                "middlebrook_stable": result.middlebrook_stable,
                "notes": result.notes,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"design_dm_input_filter failed: {e}", tool_version=__version__)


@mcp.tool(
    description=(
        "Predict conducted-emission spectrum at the LISN port for an SMPS "
        "and compare to CISPR 22 / 32 limits (Class A / B, QP / AVG detector). "
        "Models the switching node as a trapezoidal voltage waveform with "
        "duty cycle and rise time. Optional input-filter rolloff applied."
    ),
)
def predict_conducted_emissions(
    f_switching_hz: Annotated[float, Field(gt=0)],
    switch_voltage_v: Annotated[float, Field(gt=0)],
    rise_time_s: Annotated[float, Field(gt=0)],
    duty_cycle: Annotated[float, Field(gt=0, lt=1)] = 0.5,
    n_harmonics: Annotated[int, Field(gt=0, le=10000)] = 100,
    filter_attenuation_db_at_f_sw: Annotated[float, Field(ge=0)] = 0.0,
    filter_attenuation_slope_db_per_decade: Annotated[float, Field(ge=0)] = 40.0,
    cispr_class: str = "class_b",
    cispr_detector: str = "qp",
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        result = _predict_conducted_emissions(
            f_switching_hz=f_switching_hz,
            duty_cycle=duty_cycle,
            switch_voltage_v=switch_voltage_v,
            rise_time_s=rise_time_s,
            n_harmonics=n_harmonics,
            filter_attenuation_db_at_f_sw=filter_attenuation_db_at_f_sw,
            filter_attenuation_slope_db_per_decade=filter_attenuation_slope_db_per_decade,
            cispr_class=cispr_class,  # type: ignore[arg-type]
            cispr_detector=cispr_detector,  # type: ignore[arg-type]
        )
        return ok(
            {
                "freq_hz": result.freq_hz.tolist(),
                "emission_dbuv": result.emission_dbuv.tolist(),
                "limit_dbuv": [None if not np.isfinite(v) else float(v) for v in result.limit_dbuv],
                "margin_db": [None if not np.isfinite(v) else float(v) for v in result.margin_db],
                "cispr_class": result.cispr_class,
                "cispr_detector": result.cispr_detector,
                "worst_margin_db": result.worst_margin_db,
                "worst_margin_freq_hz": result.worst_margin_freq_hz,
                "pass_status": result.pass_status,
                "n_harmonics": result.n_harmonics,
                "notes": result.notes,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"predict_conducted_emissions failed: {e}", tool_version=__version__)


@mcp.tool(
    description=(
        "Design an RC snubber that damps switch-node ringing. Inputs: "
        "parasitic loop inductance, switch C_oss, peak switch voltage, "
        "switching frequency. Returns R, C, ring frequency, damping factor, "
        "and per-cycle dissipation."
    ),
)
def design_rc_snubber(
    parasitic_l_h: Annotated[float, Field(gt=0)],
    coss_f: Annotated[float, Field(gt=0)],
    peak_voltage_v: Annotated[float, Field(ge=0)],
    f_switching_hz: Annotated[float, Field(gt=0)],
    target_damping: Annotated[float, Field(gt=0, le=1.0)] = 0.7,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        result = _design_rc_snubber(
            parasitic_l_h=parasitic_l_h,
            coss_f=coss_f,
            peak_voltage_v=peak_voltage_v,
            f_switching_hz=f_switching_hz,
            target_damping=target_damping,
        )
        return ok(
            {
                "R_ohm": result.R_ohm,
                "C_f": result.C_f,
                "f_ring_hz": result.f_ring_hz,
                "damping_factor": result.damping_factor,
                "dissipation_w": result.dissipation_w,
                "notes": result.notes,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"design_rc_snubber failed: {e}", tool_version=__version__)


@mcp.tool(
    description=(
        "Pick a common-mode choke from a curated catalogue (Würth WE-CMB, "
        "TDK ZJYS / ACT, Murata DLW). Filters by DC current rating, target "
        "CM impedance at the design frequency, and DM-leakage cap."
    ),
)
def design_cm_choke(
    i_dc_a: Annotated[float, Field(ge=0)],
    target_z_cm_ohm: Annotated[float, Field(gt=0)],
    target_freq_hz: Annotated[float, Field(gt=0)] = 1e6,
    max_dm_leakage_h: Annotated[float, Field(gt=0)] = 50e-6,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        result = _design_cm_choke(
            i_dc_a=i_dc_a,
            target_z_cm_ohm=target_z_cm_ohm,
            target_freq_hz=target_freq_hz,
            max_dm_leakage_h=max_dm_leakage_h,
        )
        chosen_dict = None
        if result.chosen is not None:
            chosen_dict = {
                "part_number": result.chosen.part_number,
                "L_cm_h": result.chosen.L_cm_h,
                "L_dm_leakage_h": result.chosen.L_dm_leakage_h,
                "i_dc_max_a": result.chosen.i_dc_max_a,
                "z_cm_at_1mhz_ohm": result.chosen.z_cm_at_1mhz_ohm,
                "package": result.chosen.package,
            }
        return ok(
            {
                "chosen": chosen_dict,
                "candidates": [
                    {
                        "part_number": c.part_number,
                        "L_cm_h": c.L_cm_h,
                        "L_dm_leakage_h": c.L_dm_leakage_h,
                        "i_dc_max_a": c.i_dc_max_a,
                        "z_cm_at_1mhz_ohm": c.z_cm_at_1mhz_ohm,
                        "package": c.package,
                    }
                    for c in result.candidates
                ],
                "target_z_cm_ohm": result.target_z_cm_ohm,
                "target_freq_hz": result.target_freq_hz,
                "notes": result.notes,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"design_cm_choke failed: {e}", tool_version=__version__)


# ----- Digital + mixed-signal ---------------------------------------------


@mcp.tool(description="Setup/hold timing check on a synchronous digital path.")
def check_setup_hold(
    name: str,
    clk_period_ns: Annotated[float, Field(gt=0)],
    t_clk_q_ns: Annotated[float, Field(ge=0)],
    t_comb_ns: Annotated[float, Field(ge=0)],
    t_setup_ns: Annotated[float, Field(ge=0)],
    t_hold_ns: Annotated[float, Field(ge=0)],
    t_skew_ns: float = 0.0,
    t_jitter_ns: Annotated[float, Field(ge=0)] = 0.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        path = TimingPath(
            name=name,
            clk_period_ns=clk_period_ns,
            t_clk_q_ns=t_clk_q_ns,
            t_comb_ns=t_comb_ns,
            t_setup_ns=t_setup_ns,
            t_hold_ns=t_hold_ns,
            t_skew_ns=t_skew_ns,
            t_jitter_ns=t_jitter_ns,
        )
        r = _check_setup_hold(path)
        return ok(
            {
                "setup_slack_ns": r.setup_slack_ns,
                "hold_slack_ns": r.hold_slack_ns,
                "setup_status": r.setup_status,
                "hold_status": r.hold_status,
                "max_safe_clock_mhz": r.max_safe_clock_mhz,
                "notes": r.notes,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"check_setup_hold failed: {e}", tool_version=__version__)


@mcp.tool(description="Estimate combinational propagation delay (gates + wires + fanout).")
def propagation_delay(
    n_gates: Annotated[int, Field(gt=0)],
    t_gate_avg_ns: Annotated[float, Field(gt=0)],
    t_wire_per_mm_ns: Annotated[float, Field(ge=0)] = 0.005,
    wire_length_mm: Annotated[float, Field(ge=0)] = 0.0,
    fanout: Annotated[int, Field(ge=1)] = 1,
    t_per_fanout_ns: Annotated[float, Field(ge=0)] = 0.05,
) -> Envelope[dict[str, float]]:
    return _wrap(
        _prop_delay,
        n_gates=n_gates,
        t_gate_avg_ns=t_gate_avg_ns,
        t_wire_per_mm_ns=t_wire_per_mm_ns,
        wire_length_mm=wire_length_mm,
        fanout=fanout,
        t_per_fanout_ns=t_per_fanout_ns,
    )


@mcp.tool(description="Estimate digital-to-analog crosstalk via mutual capacitance.")
def estimate_digital_to_analog_crosstalk(
    aggressor_swing_v: Annotated[float, Field(gt=0)],
    aggressor_rise_time_ns: Annotated[float, Field(gt=0)],
    aggressor_load_pf: Annotated[float, Field(gt=0)],
    aggressor_switching_freq_mhz: Annotated[float, Field(gt=0)],
    coupling_capacitance_ff: Annotated[float, Field(gt=0)],
    victim_impedance_ohm: Annotated[float, Field(gt=0)],
    aggressor_name: str = "aggressor",
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        agg = DigitalAggressor(
            name=aggressor_name,
            swing_v=aggressor_swing_v,
            rise_time_ns=aggressor_rise_time_ns,
            switching_freq_mhz=aggressor_switching_freq_mhz,
            capacitance_load_pf=aggressor_load_pf,
        )
        return ok(
            _digital_xtalk(agg, coupling_capacitance_ff, victim_impedance_ohm),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(
            f"estimate_digital_to_analog_crosstalk failed: {e}",
            tool_version=__version__,
        )


@mcp.tool(description="Estimate supply-rail droop from digital switching activity.")
def estimate_supply_noise_injection(
    aggressor_swing_v: Annotated[float, Field(gt=0)],
    aggressor_rise_time_ns: Annotated[float, Field(gt=0)],
    aggressor_load_pf: Annotated[float, Field(gt=0)],
    aggressor_switching_freq_mhz: Annotated[float, Field(gt=0)],
    supply_inductance_nh: Annotated[float, Field(gt=0)] = 5.0,
    supply_resistance_mohm: Annotated[float, Field(ge=0)] = 10.0,
    n_simultaneous_switches: Annotated[int, Field(ge=1)] = 1,
    aggressor_name: str = "aggressor",
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        agg = DigitalAggressor(
            name=aggressor_name,
            swing_v=aggressor_swing_v,
            rise_time_ns=aggressor_rise_time_ns,
            switching_freq_mhz=aggressor_switching_freq_mhz,
            capacitance_load_pf=aggressor_load_pf,
        )
        return ok(
            _supply_noise(
                agg,
                supply_inductance_nh=supply_inductance_nh,
                supply_resistance_mohm=supply_resistance_mohm,
                n_simultaneous_switches=n_simultaneous_switches,
            ),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(
            f"estimate_supply_noise_injection failed: {e}",
            tool_version=__version__,
        )


# ----- Vendor catalogs (active devices) -----------------------------------


def _model_to_dict(m: Any) -> dict[str, Any]:
    """Dataclass -> dict (skips None)."""
    from dataclasses import asdict

    return asdict(m)


@mcp.tool(description="List all op-amp part numbers in the bundled catalog.")
def list_opamps() -> Envelope[list[str]]:
    return _wrap(_list_opamps)


@mcp.tool(description="Look up an op-amp by part number (returns full datasheet params).")
def lookup_opamp(part_number: str) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        return ok(
            _model_to_dict(_lookup_opamp(part_number)),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"lookup_opamp failed: {e}", tool_version=__version__)


@mcp.tool(
    description=(
        "Filter the op-amp catalog by spec constraints (GBW, noise, offset, "
        "supply, RRIO flags, family) and return ranked candidates."
    ),
)
def find_opamp_for_application(
    min_gbw_mhz: Annotated[float, Field(ge=0)] = 0.0,
    max_input_noise_nv_per_rthz: Annotated[float, Field(gt=0)] = 1000.0,
    max_input_offset_uv: Annotated[float, Field(gt=0)] = 1e9,
    min_supply_max_v: Annotated[float, Field(ge=0)] = 0.0,
    rail_to_rail_input: bool | None = None,
    rail_to_rail_output: bool | None = None,
    family: str | None = None,
    sort_by: str = "gbw_mhz",
) -> Envelope[list[dict[str, Any]]]:
    timer = Timer()
    try:
        results = _find_opamp(
            min_gbw_mhz=min_gbw_mhz,
            max_input_noise_nv_per_rthz=max_input_noise_nv_per_rthz,
            max_input_offset_uv=max_input_offset_uv,
            min_supply_max_v=min_supply_max_v,
            rail_to_rail_input=rail_to_rail_input,
            rail_to_rail_output=rail_to_rail_output,
            family=family,
            sort_by=sort_by,
        )
        return ok(
            [_model_to_dict(r) for r in results],
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"find_opamp_for_application failed: {e}", tool_version=__version__)


@mcp.tool(description="List all MOSFET part numbers in the bundled catalog.")
def list_mosfets() -> Envelope[list[str]]:
    return _wrap(_list_mosfets)


@mcp.tool(description="Look up a MOSFET by part number.")
def lookup_mosfet(part_number: str) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        return ok(
            _model_to_dict(_lookup_mosfet(part_number)),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"lookup_mosfet failed: {e}", tool_version=__version__)


@mcp.tool(description="Filter the MOSFET catalog by polarity, Vds, Id, Rds_on, Vgs threshold.")
def find_mosfet_for_application(
    polarity: str = "N",
    min_vds_v: Annotated[float, Field(ge=0)] = 0.0,
    min_id_a: Annotated[float, Field(ge=0)] = 0.0,
    max_rds_on_mohm: Annotated[float, Field(gt=0)] = 1e9,
    max_vgs_threshold_v: Annotated[float, Field(gt=0)] = 1e9,
    sort_by: str = "rds_on_max_mohm",
) -> Envelope[list[dict[str, Any]]]:
    timer = Timer()
    try:
        results = _find_mosfet(
            polarity=polarity,  # type: ignore[arg-type]
            min_vds_v=min_vds_v,
            min_id_a=min_id_a,
            max_rds_on_mohm=max_rds_on_mohm,
            max_vgs_threshold_v=max_vgs_threshold_v,
            sort_by=sort_by,
        )
        return ok(
            [_model_to_dict(r) for r in results],
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"find_mosfet_for_application failed: {e}", tool_version=__version__)


@mcp.tool(description="List all BJT part numbers.")
def list_bjts() -> Envelope[list[str]]:
    return _wrap(_list_bjts)


@mcp.tool(description="Look up a BJT by part number.")
def lookup_bjt(part_number: str) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        return ok(
            _model_to_dict(_lookup_bjt(part_number)),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"lookup_bjt failed: {e}", tool_version=__version__)


@mcp.tool(description="List all diode part numbers (signal / Schottky / TVS / zener / ESD).")
def list_diodes() -> Envelope[list[str]]:
    return _wrap(_list_diodes)


@mcp.tool(description="Look up a diode by part number.")
def lookup_diode(part_number: str) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        return ok(
            _model_to_dict(_lookup_diode(part_number)),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"lookup_diode failed: {e}", tool_version=__version__)


@mcp.tool(description="List all voltage reference part numbers.")
def list_references() -> Envelope[list[str]]:
    return _wrap(_list_refs)


@mcp.tool(description="Look up a voltage reference by part number.")
def lookup_reference(part_number: str) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        return ok(
            _model_to_dict(_lookup_ref(part_number)),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"lookup_reference failed: {e}", tool_version=__version__)


# ----- Filter order comparison (the most-shippable picker) ----------------


@mcp.tool(
    description=(
        "Run the synthesize -> place zeros -> vendor-snap -> optimize -> MC "
        "yield workflow for several filter orders side-by-side and return "
        "the most shippable. Default scoring favors all-pass + high yield + "
        "low SRF risk + few components."
    ),
)
def compare_filter_orders(
    orders: list[int],
    cutoff_hz: Annotated[float, Field(gt=0)],
    spec: dict[str, Any],
    zero_targets_hz: list[float],
    ripple_db: Annotated[float, Field(gt=0)] = 0.1,
    stopband_atten_db: Annotated[float, Field(gt=0)] = 50.0,
    z0: Annotated[float, Field(gt=0)] = 50.0,
    inductor_vendor: str = "coilcraft_0402hp",
    capacitor_vendor: str = "murata_gjm_c0g",
    optimize_max_iter: Annotated[int, Field(gt=0, le=10000)] = 1500,
    passband_weight: Annotated[float, Field(gt=0)] = 30.0,
    mc_n_runs: Annotated[int, Field(gt=0, le=10000)] = 1000,
    mc_tolerance_pct: Annotated[float, Field(gt=0, le=20)] = 2.0,
    s2p_dir: str | None = None,
    srf_margin: Annotated[float, Field(ge=0)] = 0.0,
    max_value_drift_pct: Annotated[float | None, Field(gt=0)] = None,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        result = _compare_orders(
            orders=orders,
            cutoff_hz=cutoff_hz,
            spec=spec,
            zero_targets_hz=zero_targets_hz,
            ripple_db=ripple_db,
            stopband_atten_db=stopband_atten_db,
            z0=z0,
            inductor_vendor=inductor_vendor,
            capacitor_vendor=capacitor_vendor,
            optimize_max_iter=optimize_max_iter,
            passband_weight=passband_weight,
            mc_n_runs=mc_n_runs,
            mc_tolerance_pct=mc_tolerance_pct,
            s2p_dir=s2p_dir,
            srf_margin=srf_margin,
            max_value_drift_pct=max_value_drift_pct,
        )
        return ok(
            {
                "orders_evaluated": result.orders_evaluated,
                "winner_order": result.winner_order,
                "winner_rationale": result.winner_rationale,
                "results": [
                    {
                        "order": r.order,
                        "n_components": r.n_components,
                        "n_traps_used": r.n_traps_used,
                        "components": r.components,
                        "spec_overall": r.spec_overall,
                        "criteria": r.criteria,
                        "srf_severity": r.srf_severity,
                        "n_srf_flagged": r.n_srf_flagged,
                        "mc_yield_pct": r.mc_yield_pct,
                        "mc_failures": r.mc_failures,
                        "most_sensitive_component": r.most_sensitive_component,
                        "transmission_zeros": r.transmission_zeros,
                        "score": r.score,
                        "rationale": r.rationale,
                        "s2p_path": r.s2p_path,
                    }
                    for r in result.results
                ],
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"compare_filter_orders failed: {e}", tool_version=__version__)


# ----- Schematic rendering (publication-quality SVG/PNG via schemdraw) ----


@mcp.tool(
    description=(
        "Render a clean publication-quality schematic of an LC ladder "
        "filter. Output format chosen from extension (.svg or .png)."
    ),
)
def render_lc_ladder_schematic(
    components: dict[str, float],
    output_path: Annotated[str, Field(description="Output .svg or .png path.")],
    z0: Annotated[float, Field(gt=0)] = 50.0,
    transmission_zeros: bool = False,
    title: str | None = None,
) -> Envelope[dict[str, str]]:
    timer = Timer()
    try:
        out = _render_lc_schematic(
            components,
            output_path,
            z0=z0,
            transmission_zeros=transmission_zeros,
            title=title,
        )
        return ok(
            {"path": str(out)},
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(
            f"render_lc_ladder_schematic failed: {e}",
            tool_version=__version__,
        )


@mcp.tool(
    description=(
        "Parse an existing LTspice .asc and re-render it as a clean "
        "schemdraw SVG/PNG (the .asc only renders inside LTspice)."
    ),
)
def render_asc_as_schematic(
    asc_path: str,
    output_path: str,
    transmission_zeros: bool = True,
    title: str | None = None,
) -> Envelope[dict[str, str]]:
    timer = Timer()
    try:
        out = _render_asc_schematic(
            asc_path,
            output_path,
            transmission_zeros=transmission_zeros,
            title=title,
        )
        return ok(
            {"path": str(out)},
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(
            f"render_asc_as_schematic failed: {e}",
            tool_version=__version__,
        )


@mcp.tool(
    description=(
        "Bundle a design directory's artifacts (schematics, response plots, "
        "and report.md) into a single shareable PDF."
    ),
)
def build_design_report_pdf(
    design_dir: Annotated[str, Field(description="Directory containing PNGs and report.md.")],
    output_pdf: Annotated[str, Field(description="Output PDF path.")],
    title: str | None = None,
) -> Envelope[dict[str, str]]:
    timer = Timer()
    try:
        out = _build_design_report_pdf(design_dir, output_pdf, title=title)
        return ok(
            {"path": str(out)},
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(
            f"build_design_report_pdf failed: {e}",
            tool_version=__version__,
        )


@mcp.tool(
    description=(
        "Run a real SPICE simulation on a schematic and reconcile it against "
        "the closed-form analytical S2P for the same components. Reports the "
        "per-region |S21| divergence and a verdict (agree / minor_disagreement "
        "/ disagree). Use this before trusting a reported yield or margin that "
        "came only from the fast analytical preview. If no simulator is "
        "installed it returns verdict='spice_unavailable' with the analytical "
        "response rather than failing."
    ),
)
def validate_against_spice(
    asc_path: Annotated[str, Field(description="Path to the .asc schematic to simulate.")],
    components: Annotated[
        dict[str, float],
        Field(description="{refdes: value} describing the same ladder the .asc draws."),
    ],
    topology: Annotated[
        str, Field(description="'series_first' or 'shunt_first'.")
    ] = "series_first",
    kind: Annotated[
        str, Field(description="'lowpass', 'highpass', 'bandpass' or 'bandstop'.")
    ] = "lowpass",
    z0: Annotated[float, Field(gt=0)] = 50.0,
    passband_threshold_db: Annotated[float, Field(gt=0)] = 0.5,
    stopband_threshold_db: Annotated[float, Field(gt=0)] = 3.0,
    prefer: Annotated[str | None, Field(description="'ltspice' | 'ngspice' | null (auto).")] = None,
    output_spice_s2p: str | None = None,
    output_analytical_s2p: str | None = None,
    timeout_sec: Annotated[float, Field(gt=0, le=600)] = 120.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        result = _validate_against_spice(
            asc_path,
            components,
            topology=topology,
            kind=kind,
            z0=z0,
            passband_threshold_db=passband_threshold_db,
            stopband_threshold_db=stopband_threshold_db,
            prefer=prefer,
            timeout_sec=timeout_sec,
        )

        if output_analytical_s2p and result.analytical_network is not None:
            write_touchstone(result.analytical_network, output_analytical_s2p)
        if output_spice_s2p and result.spice_network is not None:
            write_touchstone(result.spice_network, output_spice_s2p)

        payload = _validation_payload(result, top_n_points=10)
        if output_analytical_s2p and result.analytical_network is not None:
            payload["analytical_s2p_path"] = str(output_analytical_s2p)
        if output_spice_s2p and result.spice_network is not None:
            payload["spice_s2p_path"] = str(output_spice_s2p)

        env: Envelope[dict[str, Any]] = ok(
            payload, runtime_sec=timer.elapsed(), tool_version=__version__
        )
        if result.note:
            env.warnings.append(result.note)
        if result.verdict.value == "disagree":
            env.warnings.append(
                "SPICE and the analytical preview disagree in the passband; the "
                "analytical yield/margin numbers should not be trusted for this design."
            )
        return env
    except Exception as e:
        return error(f"validate_against_spice failed: {e}", tool_version=__version__)


@mcp.tool(
    description=(
        "Index a directory of user-supplied vendor models (.s2p / .lib) so "
        "they appear as substitution candidates under a namespace. After "
        "registering, substitute_real_components(inductor_vendor='<namespace>', "
        "...) uses them like any curated series. Kind (L/C), value and SRF are "
        "recovered from the measured reactance (series-through fixture) and the "
        "filename shorthand (e.g. part_L_3n3.s2p). Re-registering a directory "
        "refreshes the index. Per-file errors are reported, not fatal."
    ),
)
def register_user_vendor_dir(
    directory: Annotated[str, Field(description="Directory of .s2p / .lib model files.")],
    namespace: Annotated[
        str, Field(description="Label for this set, e.g. 'user' or 'user_wurth'.")
    ] = "user",
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        result = _register_user_vendor_dir(directory, namespace=namespace)
        env: Envelope[dict[str, Any]] = ok(
            result, runtime_sec=timer.elapsed(), tool_version=__version__
        )
        for err in result.get("errors", []):
            env.warnings.append(f"{err['file']}: {err['error']}")
        return env
    except Exception as e:
        return error(f"register_user_vendor_dir failed: {e}", tool_version=__version__)


# ---------------------------------------------------------------------------
# Tool namespacing — register namespaced aliases alongside the flat names
# ---------------------------------------------------------------------------
#
# Categories help LLM agents discover tools by domain. Both the flat name
# (back-compat) and the namespaced alias work; over time the namespaced
# form is preferred. A future major release will deprecate the flat names.

NAMESPACE_ALIASES: dict[str, str] = {
    # filter.* — RF / lumped-LC filter design + analysis
    "synthesize_lc_filter": "filter.synthesize_lc",
    "synthesize_lc_hpf_filter": "filter.synthesize_lc_hpf",
    "synthesize_lc_bpf_filter": "filter.synthesize_lc_bpf",
    "synthesize_lc_bsf_filter": "filter.synthesize_lc_bsf",
    "place_transmission_zero": "filter.place_transmission_zero",
    "find_transmission_zeros": "filter.find_transmission_zeros",
    "evaluate_filter_spec_tool": "filter.evaluate_spec",
    "render_response": "filter.render_response",
    "substitute_real_components": "filter.substitute_real_components",
    "list_vendor_parts": "filter.list_vendor_parts",
    "optimize_filter": "filter.optimize",
    "monte_carlo_analysis": "filter.monte_carlo",
    "stability_check": "filter.stability_check",
    "validate_against_spice": "filter.validate_against_spice",
    "register_user_vendor_dir": "filter.register_user_vendor_dir",
    "parameter_sweep": "filter.parameter_sweep",
    "corner_analysis": "filter.corner_analysis",
    "sensitivity_analysis": "filter.sensitivity",
    "srf_audit": "filter.srf_audit",
    "compare_filter_orders": "filter.compare_orders",
    "render_lc_ladder_schematic": "filter.render_lc_schematic",
    "render_asc_as_schematic": "filter.render_schematic",
    "build_design_report_pdf": "filter.build_report_pdf",
    # analog.* — active-filter / op-amp synthesis
    "sallen_key_low_pass": "analog.sallen_key_lpf",
    "sallen_key_high_pass": "analog.sallen_key_hpf",
    "sallen_key_band_pass": "analog.sallen_key_bpf",
    "mfb_low_pass": "analog.mfb_lpf",
    "mfb_band_pass": "analog.mfb_bpf",
    "cascaded_lpf_design": "analog.cascaded_lpf",
    # power.* — SMPS, LDO, control-loop analysis, EMC pre-compliance
    "analyze_ldo": "power.analyze_ldo",
    "required_psrr_for_ripple": "power.required_psrr",
    "design_buck": "power.design_buck",
    "design_boost": "power.design_boost",
    "type2_compensator": "power.type2_compensator",
    "compute_phase_margin": "power.compute_phase_margin",
    "design_pi_output_filter": "power.design_pi_output_filter",
    "design_dm_input_filter": "power.design_dm_input_filter",
    "predict_conducted_emissions": "power.predict_conducted_emissions",
    "design_rc_snubber": "power.design_rc_snubber",
    "design_cm_choke": "power.design_cm_choke",
    # digital.* — timing, crosstalk, supply-noise injection
    "check_setup_hold": "digital.check_setup_hold",
    "propagation_delay": "digital.propagation_delay",
    "estimate_digital_to_analog_crosstalk": "digital.digital_to_analog_xtalk",
    "estimate_supply_noise_injection": "digital.supply_noise_injection",
    # vendor.* — opamp / mosfet / bjt / diode / vref catalogues
    "list_opamps": "vendor.list_opamps",
    "lookup_opamp": "vendor.lookup_opamp",
    "find_opamp_for_application": "vendor.find_opamp",
    "list_mosfets": "vendor.list_mosfets",
    "lookup_mosfet": "vendor.lookup_mosfet",
    "find_mosfet_for_application": "vendor.find_mosfet",
    "list_bjts": "vendor.list_bjts",
    "lookup_bjt": "vendor.lookup_bjt",
    "list_diodes": "vendor.list_diodes",
    "lookup_diode": "vendor.lookup_diode",
    "list_references": "vendor.list_references",
    "lookup_reference": "vendor.lookup_reference",
    # sim.* — simulator runner / S-parameter extraction
    "run_simulation": "sim.run",
    "extract_sparameters": "sim.extract_sparameters",
}


def _register_namespaced_aliases() -> None:
    """Iterate the alias map and register each namespaced name as a second
    entry pointing to the same Python function as the flat name.

    Skips silently if a flat name isn't actually defined in this module
    (e.g. partial test-time imports), so the bookkeeping is robust to
    minor module surface changes.
    """
    for flat_name, namespaced_name in NAMESPACE_ALIASES.items():
        func = globals().get(flat_name)
        if func is None or not callable(func):
            continue
        mcp.tool(
            name=namespaced_name,
            description=(
                f"Namespaced alias of `{flat_name}`. Prefer the namespaced "
                "name; the flat alias will be deprecated in a future major "
                "release. See CHANGELOG."
            ),
        )(func)


_register_namespaced_aliases()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server on stdio."""
    log.info("starting mcp-ltspice", extra={"version": __version__})
    mcp.run()


if __name__ == "__main__":
    main()
