"""FastMCP server entry point for mcp-ltspice."""

from __future__ import annotations

import base64
import json
import tempfile
from collections.abc import Callable
from dataclasses import asdict
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
    from_ltspice_value,
    generate_lpf_asc,
    generate_port2_excitation_asc,
    read_components,
    update_component,
)
from mcp_ltspice.capabilities import (
    probe_spice_backend as _probe_spice_backend,
)
from mcp_ltspice.capabilities import spice_capabilities as _spice_capabilities
from mcp_ltspice.circuit_io import export_ltspice_asc, import_ltspice_asc
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
    extract_two_sweep_sparams,
    ladder_sparams_from_components,
)
from mcp_ltspice.find_zeros import find_transmission_zeros as _find_zeros
from mcp_ltspice.ir_optimize import SpiceDatasetEvaluator, TraceMetric
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
from mcp_ltspice.realized_netlist import generate_realized_filter_netlist
from mcp_ltspice.render import render_response as _render_response
from mcp_ltspice.report_pdf import build_design_report_pdf as _build_design_report_pdf
from mcp_ltspice.runner import Simulator
from mcp_ltspice.runner import run_simulation as _run_simulation
from mcp_ltspice.schematic_render import (
    render_generated_lc_ladder_asc as _render_generated_lc_ladder_asc,
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
from mcp_ltspice.validate import build_two_sweep_spice_network as _build_two_sweep_spice_network
from mcp_ltspice.validate import result_to_payload as _validation_payload
from mcp_ltspice.validate import validate_against_spice as _validate_against_spice
from mcp_ltspice.vendor_fetch import register_user_vendor_dir as _register_user_vendor_dir
from mcp_ltspice.vendor_models import (
    ComponentModel,
    ComponentSearchQuery,
)
from mcp_ltspice.vendor_models import (
    attach_component_models as _attach_component_models,
)
from mcp_ltspice.vendor_models import (
    list_vendor_parts as _list_vendor_parts,
)
from mcp_ltspice.vendor_models import (
    search_component_models as _search_component_models,
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
from rf_mcp_common.circuit_ir import CircuitAnalysis, CircuitDocument
from rf_mcp_common.envelope import Envelope, Timer, error, ok
from rf_mcp_common.jobs import DurableJobManager, JobContext, WorkspaceStore
from rf_mcp_common.logging import get_logger
from rf_mcp_common.optimization import (
    OptimizationProblem,
    render_design_change_report,
)
from rf_mcp_common.optimization import (
    optimize_circuit as _optimize_circuit_ir,
)
from rf_mcp_common.protocol import prepare_protocol_tools, run_stdio_server
from rf_mcp_common.simulation_workspace import SimulationWorkspace
from rf_mcp_common.spice_io import export_spice_file, parse_spice_file
from rf_mcp_common.tool_annotations import DEFAULT_TOOL_ANNOTATIONS
from rf_mcp_common.tool_errors import EnvelopeErrorMiddleware
from rf_mcp_common.touchstone import network_to_touchstone, write_touchstone

mcp = FastMCP(
    name="mcp-ltspice",
    version=__version__,
)
mcp.add_middleware(EnvelopeErrorMiddleware())
log = get_logger("mcp_ltspice.server")
_WORKSPACES = WorkspaceStore()
_JOBS = DurableJobManager(_WORKSPACES, max_workers=4)


def _simulation_job_handler(
    context: JobContext,
    payload: dict[str, Any],
) -> dict[str, Any]:
    workspace_id = str(payload["workspace_id"])
    artifact = _WORKSPACES.artifact(workspace_id, str(payload["artifact_id"]))
    source = Path(artifact["path"])
    run_workspace = SimulationWorkspace.create(
        "spice-job",
        parent=_JOBS.job_root(context.job_id) / "runs",
    )
    snapshot = run_workspace.snapshot_simulation_tree(
        source,
        allowed_root=source.parent,
    )
    prefer = payload.get("prefer")
    run_workspace.start(
        ["spice-backend", str(snapshot)],
        cwd=run_workspace.root,
        environment={},
        executable="auto-selected-spice-backend",
        backend_version=None,
    )
    try:
        result = _run_simulation(
            snapshot,
            prefer=Simulator(prefer) if prefer else None,
            timeout=float(payload.get("timeout_sec", 120.0)),
            sandbox=not bool(payload.get("trusted_local", False)),
            cancel_requested=context.cancelled,
        )
        run_workspace.record_artifact(result.raw_path, role="simulator_raw")
        run_workspace.record_artifact(result.log_path, role="simulator_log")
        run_workspace.complete(returncode=result.returncode)
    except Exception as exc:
        run_workspace.fail(str(exc))
        raise
    raw = _WORKSPACES.add_generated(
        workspace_id,
        result.raw_path,
        media_type="application/x-spice-raw",
    )
    log_artifact = _WORKSPACES.add_generated(
        workspace_id,
        result.log_path,
        media_type="text/plain",
    )
    return {
        "simulator": result.simulator.value,
        "returncode": result.returncode,
        "sandboxed": result.sandboxed,
        "raw_artifact_id": raw["artifact_id"],
        "log_artifact_id": log_artifact["artifact_id"],
        "run_manifest": str(run_workspace.manifest_path),
    }


def _analysis_job_handler(
    context: JobContext,
    payload: dict[str, Any],
) -> dict[str, Any]:
    operation = str(payload["analysis"])
    arguments = dict(payload["arguments"])

    def progress(completed: int, total: int) -> None:
        context.update_progress(completed, total, operation)

    if operation == "optimize_filter":
        runner: Callable[..., Any] = _optimize
    elif operation == "monte_carlo_analysis":
        runner = _monte_carlo
    elif operation == "parameter_sweep":
        runner = _parameter_sweep
    else:
        raise ValueError(f"unsupported analysis job: {operation}")
    result = runner(
        **arguments,
        cancel_requested=context.cancelled,
        progress=progress,
    )
    output = asdict(result)
    workspace_id = payload.get("workspace_id")
    if workspace_id:
        for key in ("trace_path", "points_artifact"):
            path = output.get(key)
            if path:
                record = _WORKSPACES.add_generated(
                    str(workspace_id),
                    path,
                    media_type="application/x-ndjson",
                )
                output[f"{key}_artifact_id"] = record["artifact_id"]
    return output


def _circuit_optimization_job_handler(
    context: JobContext,
    payload: dict[str, Any],
) -> dict[str, Any]:
    problem = OptimizationProblem.model_validate(payload["problem"])
    analysis = CircuitAnalysis.model_validate(payload["analysis"])
    metrics = [TraceMetric.model_validate(item) for item in payload["metrics"]]
    trusted_local = bool(payload.get("trusted_local", False))
    timeout_sec = float(payload.get("timeout_sec", 120.0))
    job_root = _JOBS.job_root(context.job_id)
    screening = SpiceDatasetEvaluator(
        backend=str(payload["screening_backend"]),  # type: ignore[arg-type]
        analysis=analysis,
        metrics=metrics,
        workspace_root=job_root / "screening",
        timeout_sec=timeout_sec,
        sandbox=not trusted_local,
    )
    validation_backend = payload.get("validation_backend")
    validation = (
        SpiceDatasetEvaluator(
            backend=str(validation_backend),  # type: ignore[arg-type]
            analysis=analysis,
            metrics=metrics,
            workspace_root=job_root / "validation",
            timeout_sec=timeout_sec,
            sandbox=not trusted_local,
        )
        if validation_backend is not None
        else None
    )

    def progress(completed: int, total: int) -> None:
        context.update_progress(completed, total, "circuit optimization")

    result = _optimize_circuit_ir(
        problem,
        screening,
        validation_evaluator=validation,
        cancel_requested=context.cancelled,
        progress=progress,
    )
    result_path = job_root / "optimization-result.json"
    result_path.write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path = job_root / "design-change-report.md"
    report_path.write_text(render_design_change_report(result), encoding="utf-8")
    output: dict[str, Any] = {
        "best_values": result.best_values,
        "report": result.report.model_dump(mode="json"),
    }
    workspace_id = payload.get("workspace_id")
    if workspace_id:
        result_artifact = _WORKSPACES.add_generated(
            str(workspace_id),
            result_path,
            media_type="application/json",
        )
        report_artifact = _WORKSPACES.add_generated(
            str(workspace_id),
            report_path,
            media_type="text/markdown",
        )
        output.update(
            {
                "result_artifact_id": result_artifact["artifact_id"],
                "result_resource_uri": (
                    f"artifact://{workspace_id}/{result_artifact['artifact_id']}"
                ),
                "report_artifact_id": report_artifact["artifact_id"],
                "report_resource_uri": (
                    f"artifact://{workspace_id}/{report_artifact['artifact_id']}"
                ),
            }
        )
    return output


_JOBS.register("simulation", _simulation_job_handler)
_JOBS.register("analysis", _analysis_job_handler)
_JOBS.register("circuit_optimization", _circuit_optimization_job_handler)


#: `spec` on every filter tool is validated as `eval.FilterSpec`, whose shape the
#: MCP schema could not previously express -- the parameter was a bare
#: `dict[str, Any]`, so a caller saw `{"type": "object"}` and had to guess. They
#: guessed wrong in predictable ways (`{"kind": ..., "s21_min_at_10ghz_db": ...}`,
#: `{"fc_hz": ..., "attenuation_target_db": ...}`), and every one of those failed
#: validation on the missing `passband`. Spelling the contract out here is the fix.
_FILTER_SPEC_DESC = (
    "Filter spec. REQUIRED key 'passband': "
    "{'f_start': Hz, 'f_stop': Hz, 'il_max_db': positive dB, 'rl_min_db': positive dB}. "
    "OPTIONAL 'stopband_targets': list of "
    "{'freq': Hz, 'rejection_min_db': positive dB, 'label': str}. "
    "Example: {'passband': {'f_start': 1e6, 'f_stop': 7e8, 'il_max_db': 1.0, "
    "'rl_min_db': 15.0}, 'stopband_targets': [{'freq': 2.4e9, "
    "'rejection_min_db': 30.0, 'label': '2f0'}]}. "
    "Note there is no 'kind'/'fc_hz'/'attenuation_target_db' key -- filter kind is "
    "the separate 'kind' argument."
)

#: refdes -> the list of values to try for it. The Cartesian product of every
#: listed value is evaluated, so (L1: 5 values) x (C2: 7 values) is 35 points.
#: NOT a frequency-axis description: `{'freq': {'start': ..., 'stop': ...}}` is
#: the common wrong guess and raises before anything is simulated.
_SWEEP_DESC = (
    "Component value grid: {refdes: [values to try]}, e.g. "
    "{'L1': [4.7e-9, 5.1e-9], 'C2': [5.6e-12, 6.2e-12]}. Every combination is "
    "evaluated (Cartesian product). This is NOT a frequency sweep."
)

#: refdes -> value. Base SI is what the tools want; engineering notation is
#: accepted because every prompt and datasheet in this domain writes "4.7 nH",
#: and callers reliably do too. Before `_coerce_components` those strings
#: reached the arithmetic and raised "unsupported operand type(s) for -:
#: 'float' and 'str'", which says nothing about what to send instead.
_COMPONENTS_DESC = (
    "Component values as {refdes: value}, e.g. {'L1': 4.7e-9, 'C2': 5.6e-12}. "
    "Base SI units (henries, farads) are preferred. SPICE/engineering strings "
    "are also accepted -- '4.7n', '5.6pF', '15nH', '1meg'. Note SPICE 'm' means "
    "MILLI, not mega; use 'meg' for 1e6."
)


def _coerce_components(value: object) -> object:
    """Accept engineering notation for component values.

    Reuses `asc_io.from_ltspice_value` for the suffix table rather than
    duplicating it, and additionally tolerates a trailing unit letter (F/H/ohm)
    that SPICE itself does not write but callers do: '5.6pF' -> 5.6e-12. A value
    that still will not parse is left untouched, so Pydantic reports it as the
    ordinary validation error for the field instead of failing here.
    """
    if not isinstance(value, dict):
        return value
    out: dict[str, object] = {}
    for name, raw in value.items():
        if isinstance(raw, str):
            text = raw.strip()
            for candidate in (text, text.rstrip("FfHh\u03a9\u2126").strip()):
                try:
                    out[name] = from_ltspice_value(candidate)
                    break
                except ValueError:
                    continue
            else:
                out[name] = raw
        else:
            out[name] = raw
    return out


#: The annotated form every `components` parameter uses.
#:
#: `_coerce_components` is called explicitly at the top of each tool body rather
#: than attached here as a `BeforeValidator`: fastmcp 3.4.4 builds the JSON
#: schema from the `Field` but does not run Annotated validators, so a
#: BeforeValidator here looks like protection and silently is not. Verified
#: against the registered FunctionTool, not assumed.
_Components = Annotated[dict[str, float], Field(description=_COMPONENTS_DESC)]


def _circuit_from_payload(value: dict[str, Any]) -> CircuitDocument:
    """Accept either a bare CircuitDocument or the enriched circuit_parse result."""
    return CircuitDocument.model_validate(
        {key: item for key, item in value.items() if key in CircuitDocument.model_fields}
    )


@mcp.resource("capabilities://mcp-ltspice")
def capabilities_resource() -> dict[str, Any]:
    """Current SPICE backend readiness, including last validation state."""
    return _spice_capabilities()


@mcp.resource("artifact://{workspace_id}/{artifact_id}")
def artifact_resource(workspace_id: str, artifact_id: str) -> bytes:
    """Read a checksum-verified, bounded workspace artifact."""
    return _WORKSPACES.read(workspace_id, artifact_id)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Administrative readiness probe for LTspice or ngspice. Distinguishes "
        "installed, launchable, and known-answer validated states."
    ),
)
def probe_backend(
    backend: Annotated[str, Field(description="'ltspice' or 'ngspice'")],
    validate: bool = True,
    timeout_sec: Annotated[float, Field(gt=0, le=120)] = 20.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        return ok(
            _probe_spice_backend(
                backend,
                validate=validate,
                timeout_sec=timeout_sec,
            ),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as exc:
        return error(f"probe_backend failed: {exc}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Create a durable server-owned workspace and return its opaque ID.",
)
def workspace_create(label: str | None = None) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        return ok(
            _WORKSPACES.create(label),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as exc:
        return error(f"workspace_create failed: {exc}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Import a local artifact into a durable workspace. SPICE inputs copy "
        "and validate their complete static include graph by default."
    ),
)
def artifact_import(
    workspace_id: str,
    source_path: str,
    include_dependencies: bool = True,
    media_type: str = "application/octet-stream",
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        source = Path(source_path)
        spice_suffixes = {".asc", ".cir", ".net", ".sp", ".spice"}
        record = (
            _WORKSPACES.import_spice_tree(workspace_id, source)
            if include_dependencies and source.suffix.lower() in spice_suffixes
            else _WORKSPACES.import_file(
                workspace_id,
                source,
                media_type=media_type,
            )
        )
        return ok(
            record | {"resource_uri": (f"artifact://{workspace_id}/{record['artifact_id']}")},
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as exc:
        return error(f"artifact_import failed: {exc}", tool_version=__version__)


def _parse_circuit_artifact(workspace_id: str, artifact_id: str) -> CircuitDocument:
    record = _WORKSPACES.artifact(workspace_id, artifact_id)
    path = Path(record["path"])
    suffix = path.suffix.lower()
    if suffix == ".asc":
        return import_ltspice_asc(path)
    if suffix not in {".cir", ".net", ".sp", ".spice", ".lib", ".mod"}:
        raise ValueError(f"unsupported circuit artifact suffix: {suffix}")
    return parse_spice_file(path, dialect="spice")


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Parse an imported LTspice ASC or SPICE artifact into versioned "
        "CircuitDocument IR with explicit pin-to-net connectivity. Every "
        "unsupported construct is returned with a source location."
    ),
)
def circuit_parse(
    workspace_id: str,
    artifact_id: str,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        document = _parse_circuit_artifact(workspace_id, artifact_id)
        payload = document.model_dump(mode="json")
        payload.update(
            {
                "format": (
                    "ltspice_asc" if document.source_format == "ltspice_asc" else "spice_netlist"
                ),
                "source_artifact_id": artifact_id,
                "is_supported": document.is_supported,
                "unsupported_constructs": [
                    item.model_dump(mode="json") for item in document.unsupported
                ],
                "connectivity_signature": document.connectivity_signature(),
                "electrical_fingerprint": document.electrical_fingerprint(),
            }
        )
        return ok(
            payload,
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as exc:
        return error(f"circuit_parse failed: {exc}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Validate an imported circuit graph and return all unsupported "
        "construct diagnostics. require_lossless rejects any blocking diagnostic."
    ),
)
def circuit_validate(
    workspace_id: str,
    artifact_id: str,
    require_lossless: bool = False,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        document = _parse_circuit_artifact(workspace_id, artifact_id)
        blocking = [
            item.model_dump(mode="json")
            for item in document.unsupported
            if item.severity == "error"
        ]
        valid = document.is_supported
        return ok(
            {
                "valid": valid,
                "accepted": valid or not require_lossless,
                "require_lossless": require_lossless,
                "diagnostics": [item.model_dump(mode="json") for item in document.unsupported],
                "blocking_diagnostics": blocking,
                "connectivity_signature": document.connectivity_signature(),
                "electrical_fingerprint": document.electrical_fingerprint(),
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as exc:
        return error(f"circuit_validate failed: {exc}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Export a supported CircuitDocument to normalized SPICE or back to its "
        "preserved LTspice ASC drawing. The generated artifact is checksum-addressed."
    ),
)
def circuit_export(
    workspace_id: str,
    circuit: dict[str, Any],
    output_format: Annotated[str, Field(description="'spice' or 'ltspice_asc'")],
    name: str | None = None,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        document = _circuit_from_payload(circuit)
        document.require_supported()
        if output_format not in {"spice", "ltspice_asc"}:
            raise ValueError("output_format must be 'spice' or 'ltspice_asc'")
        suffix = ".cir" if output_format == "spice" else ".asc"
        safe_name = Path(name or f"{document.document_id}{suffix}").name
        if Path(safe_name).suffix.lower() != suffix:
            safe_name += suffix
        with tempfile.TemporaryDirectory(prefix="rf-mcp-export-") as temporary:
            target = Path(temporary) / safe_name
            if output_format == "spice":
                export_spice_file(document, target, preserve_source=False)
            else:
                export_ltspice_asc(document, target)
            record = _WORKSPACES.add_generated(
                workspace_id,
                target,
                name=safe_name,
                media_type=(
                    "application/x-spice-netlist"
                    if output_format == "spice"
                    else "application/x-ltspice-schematic"
                ),
            )
        return ok(
            record
            | {
                "resource_uri": (f"artifact://{workspace_id}/{record['artifact_id']}"),
                "electrical_fingerprint": document.electrical_fingerprint(),
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as exc:
        return error(f"circuit_export failed: {exc}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Submit a durable, cancellable SPICE simulation job. Safe mode uses "
        "an immutable snapshot and verified OS sandbox; trusted_local opts out."
    ),
)
def simulation_submit(
    workspace_id: str,
    artifact_id: str,
    prefer: Annotated[str | None, Field(description="'ltspice' or 'ngspice'")] = None,
    timeout_sec: Annotated[float, Field(gt=0, le=600)] = 120.0,
    trusted_local: bool = False,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        _WORKSPACES.artifact(workspace_id, artifact_id)
        job = _JOBS.submit(
            "simulation",
            {
                "workspace_id": workspace_id,
                "artifact_id": artifact_id,
                "prefer": prefer,
                "timeout_sec": timeout_sec,
                "trusted_local": trusted_local,
            },
            workspace_id=workspace_id,
        )
        return ok(job, runtime_sec=timer.elapsed(), tool_version=__version__)
    except Exception as exc:
        return error(f"simulation_submit failed: {exc}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Submit optimize_filter, monte_carlo_analysis, or parameter_sweep as "
        "a durable bounded job. Arguments use the corresponding canonical tool schema."
    ),
)
def analysis_submit(
    analysis: Annotated[
        str,
        Field(description=("'optimize_filter', 'monte_carlo_analysis', or 'parameter_sweep'")),
    ],
    arguments: dict[str, Any],
    workspace_id: str | None = None,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        if analysis not in {
            "optimize_filter",
            "monte_carlo_analysis",
            "parameter_sweep",
        }:
            raise ValueError("unsupported analysis job")
        job = _JOBS.submit(
            "analysis",
            {
                "analysis": analysis,
                "arguments": arguments,
                "workspace_id": workspace_id,
            },
            workspace_id=workspace_id,
        )
        return ok(job, runtime_sec=timer.elapsed(), tool_version=__version__)
    except Exception as exc:
        return error(f"analysis_submit failed: {exc}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Submit generic topology-preserving CircuitDocument optimization as a "
        "durable job. Variables address component values/parameters; objectives "
        "and constraints address named simulator trace metrics; all corners and "
        "yield samples are evaluated. A distinct validation backend produces a "
        "simulator-validated design-change report with exact model hashes."
    ),
)
def circuit_optimize_submit(
    problem: dict[str, Any],
    analysis: dict[str, Any],
    metrics: list[dict[str, Any]],
    screening_backend: Annotated[str, Field(description="'ngspice' or 'ltspice'")],
    validation_backend: Annotated[
        str | None,
        Field(description="Independent 'ngspice' or 'ltspice' backend."),
    ] = None,
    workspace_id: str | None = None,
    timeout_sec: Annotated[float, Field(gt=0, le=600)] = 120.0,
    trusted_local: bool = False,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        if screening_backend not in {"ngspice", "ltspice"}:
            raise ValueError("screening_backend must be 'ngspice' or 'ltspice'")
        if validation_backend not in {None, "ngspice", "ltspice"}:
            raise ValueError("validation_backend must be null, 'ngspice', or 'ltspice'")
        parsed_problem = OptimizationProblem.model_validate(problem)
        CircuitAnalysis.model_validate(analysis)
        if not metrics:
            raise ValueError("metrics cannot be empty")
        for metric in metrics:
            TraceMetric.model_validate(metric)
        if parsed_problem.require_independent_backend:
            if validation_backend is None:
                raise ValueError(
                    "problem requires independent validation but validation_backend is null"
                )
            if validation_backend == screening_backend:
                raise ValueError(
                    "screening_backend and validation_backend must differ for "
                    "independent validation"
                )
        job = _JOBS.submit(
            "circuit_optimization",
            {
                "problem": parsed_problem.model_dump(mode="json"),
                "analysis": analysis,
                "metrics": metrics,
                "screening_backend": screening_backend,
                "validation_backend": validation_backend,
                "workspace_id": workspace_id,
                "timeout_sec": timeout_sec,
                "trusted_local": trusted_local,
            },
            workspace_id=workspace_id,
        )
        return ok(job, runtime_sec=timer.elapsed(), tool_version=__version__)
    except Exception as exc:
        return error(f"circuit_optimize_submit failed: {exc}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Get durable job state, progress, result, and retry diagnostics.",
)
def job_get(job_id: str) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        return ok(
            _JOBS.get(job_id),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as exc:
        return error(f"job_get failed: {exc}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Request cancellation and terminate a running simulator process tree.",
)
def job_cancel(job_id: str) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        return ok(
            _JOBS.cancel(job_id),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as exc:
        return error(f"job_cancel failed: {exc}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Retry a failed or cancelled durable job using its immutable payload.",
)
def job_retry(job_id: str) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        return ok(
            _JOBS.retry(job_id),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as exc:
        return error(f"job_retry failed: {exc}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="List checksum-addressed artifacts in the workspace associated with a job.",
)
def job_list_artifacts(job_id: str) -> Envelope[list[dict[str, Any]]]:
    timer = Timer()
    try:
        job = _JOBS.get(job_id)
        workspace_id = job.get("workspace_id")
        if workspace_id is None:
            return ok([], runtime_sec=timer.elapsed(), tool_version=__version__)
        manifest = _WORKSPACES.get(str(workspace_id))
        artifacts = [
            record | {"resource_uri": (f"artifact://{workspace_id}/{record['artifact_id']}")}
            for record in manifest["artifacts"].values()
        ]
        return ok(
            artifacts,
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as exc:
        return error(f"job_list_artifacts failed: {exc}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Read a small checksum-verified artifact by opaque ID. Binary content "
        "is base64; larger artifacts must be consumed through their resource URI."
    ),
)
def artifact_read(
    workspace_id: str,
    artifact_id: str,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        record = _WORKSPACES.artifact(workspace_id, artifact_id)
        content = _WORKSPACES.read(workspace_id, artifact_id)
        return ok(
            {
                "artifact": record,
                "encoding": "base64",
                "content": base64.b64encode(content).decode("ascii"),
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as exc:
        return error(f"artifact_read failed: {exc}", tool_version=__version__)


# ---------------------------------------------------------------------------
# Tool 1: run_simulation
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
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
    trusted_in_place: Annotated[
        bool,
        Field(
            description=(
                "Opt out of immutable workspace snapshotting. Only use for trusted local files."
            )
        ),
    ] = False,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        workspace = _WORKSPACES.create("run_simulation")
        workspace_id = str(workspace["workspace_id"])
        source = _WORKSPACES.import_spice_tree(workspace_id, asc_path)
        job = _JOBS.submit(
            "simulation",
            {
                "workspace_id": workspace_id,
                "artifact_id": source["artifact_id"],
                "prefer": prefer,
                "timeout_sec": timeout_sec,
                "trusted_local": trusted_in_place,
            },
            workspace_id=workspace_id,
        )
        terminal = _JOBS.wait(str(job["job_id"]), timeout_sec=timeout_sec + 10.0)
        if terminal["status"] != "completed":
            raise RuntimeError(f"simulation job {terminal['status']}: {terminal.get('error')}")
        result = terminal["result"]
        raw = _WORKSPACES.artifact(workspace_id, result["raw_artifact_id"])
        log_artifact = _WORKSPACES.artifact(
            workspace_id,
            result["log_artifact_id"],
        )
        return ok(
            {
                "raw_path": raw["path"],
                "log_path": log_artifact["path"],
                "raw_artifact_id": raw["artifact_id"],
                "log_artifact_id": log_artifact["artifact_id"],
                "simulator": result["simulator"],
                "returncode": result["returncode"],
                "workspace_id": workspace_id,
                "job_id": terminal["job_id"],
                "job_manifest_path": str(_JOBS.job_root(str(terminal["job_id"])) / "manifest.json"),
                "input_artifact_id": source["artifact_id"],
                "trusted_in_place": trusted_in_place,
                "sandboxed": result["sandboxed"],
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Run two matched-port AC excitations on an LTspice .asc and write the "
        "measured S11/S21/S12/S22 matrix to Touchstone. The source schematic "
        "must use V1 + Rs1 to drive p1 and RL1 to terminate p2. The tool "
        "validates that fixture, constructs the port-2 excitation, uses the "
        "same backend for both sweeps, and returns extraction provenance."
    ),
)
def extract_sparameters(
    asc_path: Annotated[str, Field(description="Path to the source .asc schematic.")],
    output_s2p: Annotated[str, Field(description="Path for the output .s2p file.")],
    port_map: Annotated[
        dict[int, str] | None,
        Field(description="Map of port index to node name. Default: {1: 'p1', 2: 'p2'}."),
    ] = None,
    z0: Annotated[float, Field(gt=0)] = 50.0,
    prefer: Annotated[
        str | None,
        Field(description="Force 'ltspice' or 'ngspice'. Default: auto."),
    ] = None,
    timeout_sec: Annotated[float, Field(gt=0, le=600)] = 120.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        source = Path(asc_path).expanduser().resolve()
        requested_output = Path(output_s2p).expanduser().resolve()
        fixture = port_map or {1: "p1", 2: "p2"}
        if fixture != {1: "p1", 2: "p2"}:
            raise ValueError(
                "Automatic two-sweep generation currently requires port_map={1: 'p1', 2: 'p2'}."
            )

        workspace = SimulationWorkspace.create("spice-two-port")
        port1_asc = workspace.snapshot_simulation_tree(source)
        port2_asc = generate_port2_excitation_asc(
            port1_asc,
            workspace.root / "inputs" / "port2.asc",
            z0=z0,
        )
        workspace.record_artifact(port2_asc, role="generated_port2_excitation")

        prefer_enum = Simulator(prefer) if prefer else None
        first = _run_simulation(port1_asc, prefer=prefer_enum, timeout=timeout_sec)
        second = _run_simulation(
            port2_asc,
            prefer=first.simulator,
            timeout=timeout_sec,
        )
        net, provenance = extract_two_sweep_sparams(
            first.raw_path,
            second.raw_path,
            port_map=fixture,
            z0=z0,
        )
        out = write_touchstone(net, requested_output)
        provenance["source_files"] |= {
            "source_asc": str(source),
            "port1_asc": str(port1_asc),
            "port2_asc": str(port2_asc),
            "port1_log": str(first.log_path),
            "port2_log": str(second.log_path),
        }
        provenance["simulator"] = {
            "backend": first.simulator.value,
            "port1_returncode": first.returncode,
            "port2_returncode": second.returncode,
            "workspace": str(workspace.root),
        }
        return ok(
            {
                "s2p_path": str(out),
                "n_freq_points": int(net.f.size),
                "freq_range_hz": [float(net.f.min()), float(net.f.max())],
                "z0": z0,
                "provenance": provenance,
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Synthesize a band-pass LC ladder via the LPF→BPF frequency transformation "
        "(Pozar §8.5). Series inductors become series-LC tanks (resonant at f₀); "
        "shunt capacitors become shunt-LC tanks (parallel-resonant). Component count "
        "doubles vs. the LPF prototype. f₀ = √(f_low · f_high) (geometric mean); "
        "fractional bandwidth Δ = (f_high - f_low) / f₀. Components emitted with "
        "'_s' suffix on series-LC pairs. Elliptic (odd order ≥3) maps each LPF trap "
        "to a four-element composite shunt branch {Lk_s, Ck_s, Lk, Ck} whose two "
        "resonances are the images ω₀(√(b²+1) ± b), b = ω_z·Δ/2, of the prototype "
        "zero: notch pairs straddling the passband."
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Move the transmission zero of a shunt LC trap to a target frequency, "
        "preserving (by default) the L/C ratio for impedance match. Snaps to "
        "E24 / E96 component series. Writes a workspace copy by default; the "
        "source schematic is not modified."
    ),
)
def place_transmission_zero(
    asc_path: Annotated[str, Field(description="Path to the .asc to edit.")],
    trap_index: Annotated[int, Field(ge=2, description="Trap refdes index (e.g. 2 for L2/C2).")],
    target_freq_hz: Annotated[float, Field(gt=0)],
    preserve_ratio: bool = True,
    snap_series: Annotated[str | None, Field(description="'E24' | 'E96' | 'E192' | None.")] = "E24",
    output_asc: Annotated[
        str | None,
        Field(
            description=(
                "Optional output copy. If omitted, a workspace copy is created; "
                "the source is never modified."
            )
        ),
    ] = None,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        source = Path(asc_path).expanduser().resolve()
        if output_asc is None:
            workspace = SimulationWorkspace.create("place-transmission-zero")
            target = workspace.snapshot_simulation_tree(source)
        else:
            workspace = None
            target = Path(output_asc).expanduser().resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        comps = read_components(target)
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
        update_component(target, l_key, new_comps[l_key])
        update_component(target, c_key, new_comps[c_key])
        return ok(
            {
                "trap_index": trap_index,
                "target_freq_hz": target_freq_hz,
                "achieved_freq_hz": result["achieved_freq_hz"],
                "freq_error_pct": result["freq_error_pct"],
                "previous": result["previous"],
                "new": result["new"],
                "source_asc_path": str(source),
                "asc_path": str(target),
                "workspace": str(workspace.root) if workspace is not None else None,
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    name="evaluate_filter_spec",
    description=(
        "Evaluate a Touchstone .s2p file against a coex-aware spec. Returns "
        "pass/fail per criterion with margin in dB. Spec format: "
        "{passband: {f_start, f_stop, il_max_db, rl_min_db}, "
        "stopband_targets: [{freq, rejection_min_db, label}, ...]}."
    ),
)
def evaluate_filter_spec_tool(
    s2p_path: Annotated[str, Field(description="Path to Touchstone .s2p.")],
    spec: Annotated[dict[str, Any], Field(description=_FILTER_SPEC_DESC)],
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Snap ideal L/C values to vendor catalog parts and return first-order "
        "parasitic metadata (Cp/Ls, ESR, SRF) plus immutable model provenance. "
        "Pass the result as component_substitution to optimization, sweep, "
        "sensitivity, or Monte Carlo, or use simulate_realized_filter for "
        "model-backed SPICE validation. "
        "Vendors: 'coilcraft_0402hp', 'coilcraft_0603cs', 'murata_gjm_c0g', "
        "'johanson_l' (L-07W), 'tdk_mlg' (MLK1005S). Set srf_margin > 0 (e.g. 1.2) "
        "to auto-reject parts whose SRF < srf_margin × max_spec_freq_hz; "
        "provide either max_spec_freq_hz directly or a spec dict from which it's derived."
    ),
)
def substitute_real_components(
    components: _Components,
    inductor_vendor: str = "coilcraft_0402hp",
    capacitor_vendor: str = "murata_gjm_c0g",
    srf_margin: Annotated[float, Field(ge=0)] = 0.0,
    max_spec_freq_hz: Annotated[float | None, Field(gt=0)] = None,
    spec: Annotated[dict[str, Any] | None, Field(description=_FILTER_SPEC_DESC)] = None,
    max_value_drift_pct: Annotated[float | None, Field(gt=0)] = 25.0,
) -> Envelope[dict[str, dict[str, Any]]]:
    components = _coerce_components(components)
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


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Search curated and registered local component providers using hard "
        "constraints for value, package, orderability/stock, Q and SRF, "
        "tolerance, ratings, bias, temperature, and model kind. Unknown record "
        "fields fail requested constraints rather than matching silently."
    ),
)
def search_component_models(
    kind: Annotated[str | None, Field(description="'L', 'C', or null")] = None,
    target_value: Annotated[float | None, Field(gt=0)] = None,
    min_value: Annotated[float | None, Field(ge=0)] = None,
    max_value: Annotated[float | None, Field(gt=0)] = None,
    packages: list[str] | None = None,
    availability: Annotated[
        str,
        Field(description="'any', 'in_stock', 'orderable', or 'generic'"),
    ] = "any",
    min_q: Annotated[float | None, Field(ge=0)] = None,
    q_frequency_hz: Annotated[float | None, Field(gt=0)] = None,
    min_srf_hz: Annotated[float | None, Field(gt=0)] = None,
    max_tolerance_pct: Annotated[float | None, Field(ge=0)] = None,
    min_ratings: dict[str, float] | None = None,
    operating_bias: dict[str, float] | None = None,
    operating_temperature_c: float | None = None,
    model_kinds: list[str] | None = None,
    vendors: list[str] | None = None,
    limit: Annotated[int, Field(ge=1, le=1000)] = 50,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        query = ComponentSearchQuery(
            kind=kind,  # type: ignore[arg-type]
            target_value=target_value,
            min_value=min_value,
            max_value=max_value,
            packages=tuple(packages or ()),
            availability=availability,  # type: ignore[arg-type]
            min_q=min_q,
            q_frequency_hz=q_frequency_hz,
            min_srf_hz=min_srf_hz,
            max_tolerance_pct=max_tolerance_pct,
            min_ratings=min_ratings,
            operating_bias=operating_bias,
            operating_temperature_c=operating_temperature_c,
            model_kinds=tuple(model_kinds or ()),  # type: ignore[arg-type]
            vendors=tuple(vendors or ()),
            limit=limit,
        )
        return ok(
            _search_component_models(query).to_dict(),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as exc:
        return error(f"search_component_models failed: {exc}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Attach exact provider model records to CircuitDocument components. "
        "The returned IR records model checksums, pin maps, validity ranges, "
        "license/provenance, and orderable-versus-generic status."
    ),
)
def circuit_attach_models(
    circuit: dict[str, Any],
    selections: dict[str, dict[str, Any]],
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        document = _circuit_from_payload(circuit)
        models = {refdes: ComponentModel(**record) for refdes, record in selections.items()}
        attached = _attach_component_models(document, models)
        return ok(
            {
                "circuit": attached.model_dump(mode="json"),
                "electrical_fingerprint": attached.electrical_fingerprint(),
                "model_hashes": {
                    component.refdes: component.model.checksum_sha256
                    for component in attached.components
                    if component.model is not None
                },
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as exc:
        return error(f"circuit_attach_models failed: {exc}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Select vendor L/C models, instantiate them into two simulator-ready "
        "LPF/HPF/BPF/BSF ladder netlists, run both matched-port excitations, and "
        "write a full measured .s2p. Curated parts use their explicit "
        "first-order loss/SRF subcircuits; registered two-pin .lib parts are "
        "included verbatim. Returns model checksums and simulation provenance."
    ),
)
def simulate_realized_filter(
    components: _Components,
    output_s2p: str,
    inductor_vendor: str = "coilcraft_0402hp",
    capacitor_vendor: str = "murata_gjm_c0g",
    kind: Annotated[str, Field(description="lowpass, highpass, bandpass, or bandstop")] = "lowpass",
    topology: Annotated[str, Field(description="series_first or shunt_first")] = "series_first",
    transmission_zeros: bool | None = None,
    z0: Annotated[float, Field(gt=0)] = 50.0,
    f_start_hz: Annotated[float, Field(gt=0)] = 1e6,
    f_stop_hz: Annotated[float, Field(gt=0)] = 5e9,
    points_per_decade: Annotated[int, Field(ge=1, le=10_000)] = 200,
    prefer: Annotated[str | None, Field(description="'ltspice' | 'ngspice' | null.")] = None,
    timeout_sec: Annotated[float, Field(gt=0, le=600)] = 120.0,
) -> Envelope[dict[str, Any]]:
    components = _coerce_components(components)
    timer = Timer()
    try:
        if f_stop_hz <= f_start_hz:
            raise ValueError("f_stop_hz must be greater than f_start_hz")
        substitution = _substitute_real(
            components,
            inductor_vendor,
            capacitor_vendor,
        )
        workspace = SimulationWorkspace.create("realized-filter")
        port1, manifest1 = generate_realized_filter_netlist(
            substitution,
            workspace.root / "inputs" / "realized_port1.cir",
            kind=kind,
            topology=topology,
            transmission_zeros=transmission_zeros,
            driven_port=1,
            z0=z0,
            f_start_hz=f_start_hz,
            f_stop_hz=f_stop_hz,
            points_per_decade=points_per_decade,
        )
        port2, manifest2 = generate_realized_filter_netlist(
            substitution,
            workspace.root / "inputs" / "realized_port2.cir",
            kind=kind,
            topology=topology,
            transmission_zeros=transmission_zeros,
            driven_port=2,
            z0=z0,
            f_start_hz=f_start_hz,
            f_stop_hz=f_stop_hz,
            points_per_decade=points_per_decade,
        )
        prefer_enum = Simulator(prefer) if prefer else None
        first = _run_simulation(port1, prefer=prefer_enum, timeout=timeout_sec)
        second = _run_simulation(port2, prefer=first.simulator, timeout=timeout_sec)
        net, extraction = extract_two_sweep_sparams(
            first.raw_path,
            second.raw_path,
            port_map={1: "p1", 2: "p2"},
            z0=z0,
        )
        output = write_touchstone(net, output_s2p)
        model_fidelity = {
            refdes: selected["model"]["model_kind"] for refdes, selected in substitution.items()
        }
        return ok(
            {
                "s2p_path": str(output),
                "evaluation_mode": "simulator_validated",
                "kind": kind,
                "topology": topology,
                "model_fidelity": model_fidelity,
                "substitution": substitution,
                "model_manifest_paths": [str(manifest1), str(manifest2)],
                "netlist_paths": [str(port1), str(port2)],
                "extraction": extraction,
                "simulator": first.simulator.value,
                "workspace": str(workspace.root),
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"simulate_realized_filter failed: {e}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="List the value catalogue for a vendor part series.",
)
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Iteratively tune component values against a spec via Nelder-Mead. "
        "Loss = sum of negative spec margins (failing criteria only). Final "
        "values snapped to E24 / E96 by default."
    ),
)
def optimize_filter(
    initial_components: dict[str, float],
    spec: Annotated[dict[str, Any], Field(description=_FILTER_SPEC_DESC)],
    tune: list[str] | None = None,
    transmission_zeros: bool | None = None,
    kind: Annotated[str, Field(description="lowpass, highpass, bandpass, or bandstop")] = "lowpass",
    topology: Annotated[str, Field(description="series_first or shunt_first")] = "series_first",
    component_substitution: Annotated[
        dict[str, dict[str, Any]] | None,
        Field(description="Optional output of substitute_real_components; includes parasitics."),
    ] = None,
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
            kind=kind,
            topology=topology,
            component_substitution=component_substitution,
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
                "analysis_context": result.analysis_context,
                "estimated_objective_evaluations": result.estimated_objective_evaluations,
                "estimated_work_units": result.estimated_work_units,
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Run Monte Carlo trials with Gaussian-distributed component tolerances. "
        "Reports yield (% passing the spec) and per-metric mean/std/percentiles. "
        "Set trace=True to also emit a JSONL file with one record per trial "
        "(seed, components, metrics, passed, failures) for root-cause analysis "
        "of yield loss."
    ),
)
def monte_carlo_analysis(
    components: _Components,
    spec: Annotated[dict[str, Any], Field(description=_FILTER_SPEC_DESC)],
    tolerance_pct: dict[str, float] | float = 5.0,
    n_runs: Annotated[int, Field(gt=0, le=10_000)] = 1000,
    z0: Annotated[float, Field(gt=0)] = 50.0,
    transmission_zeros: bool | None = None,
    kind: Annotated[str, Field(description="lowpass, highpass, bandpass, or bandstop")] = "lowpass",
    topology: Annotated[str, Field(description="series_first or shunt_first")] = "series_first",
    component_substitution: Annotated[
        dict[str, dict[str, Any]] | None,
        Field(description="Optional output of substitute_real_components; includes parasitics."),
    ] = None,
    n_jobs: Annotated[int, Field(ge=-1, le=8)] = 1,
    trace: bool = False,
    trace_path: str | None = None,
) -> Envelope[dict[str, Any]]:
    components = _coerce_components(components)
    timer = Timer()
    try:
        result = _monte_carlo(
            components,
            spec,
            tolerance_pct=tolerance_pct,
            n_runs=n_runs,
            z0=z0,
            transmission_zeros=transmission_zeros,
            kind=kind,
            topology=topology,
            component_substitution=component_substitution,
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
                "analysis_context": result.analysis_context,
                "estimated_work_units": result.estimated_work_units,
                "effective_n_jobs": result.effective_n_jobs,
                "trace_path": result.trace_path,
                "trace_manifest": result.trace_manifest,
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Sweep one or more component values across a Cartesian product grid "
        "and report per-point spec margins + overall yield."
    ),
)
def parameter_sweep(
    components: _Components,
    sweep: Annotated[dict[str, list[float]], Field(description=_SWEEP_DESC)],
    spec: Annotated[dict[str, Any], Field(description=_FILTER_SPEC_DESC)],
    z0: Annotated[float, Field(gt=0)] = 50.0,
    transmission_zeros: bool | None = None,
    kind: Annotated[str, Field(description="lowpass, highpass, bandpass, or bandstop")] = "lowpass",
    topology: Annotated[str, Field(description="series_first or shunt_first")] = "series_first",
    component_substitution: Annotated[
        dict[str, dict[str, Any]] | None,
        Field(description="Optional output of substitute_real_components; includes parasitics."),
    ] = None,
    max_points: Annotated[int, Field(ge=1, le=10_000)] = 5_000,
    result_mode: Annotated[
        str,
        Field(description="'auto', 'inline', or 'artifact'. Large results default to JSONL."),
    ] = "auto",
) -> Envelope[dict[str, Any]]:
    components = _coerce_components(components)
    timer = Timer()
    try:
        result = _parameter_sweep(
            components,
            sweep,
            spec,
            z0=z0,
            transmission_zeros=transmission_zeros,
            kind=kind,
            topology=topology,
            component_substitution=component_substitution,
            max_points=max_points,
            result_mode=result_mode,  # type: ignore[arg-type]
        )
        return ok(
            {
                "n_points": result.n_points,
                "n_passing": result.n_passing,
                "yield_pct": result.yield_pct,
                "analysis_context": result.analysis_context,
                "estimated_work_units": result.estimated_work_units,
                "points_artifact": result.points_artifact,
                "artifact_manifest": result.artifact_manifest,
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Evaluate a filter spec at named corners (e.g. TT/SS/FF or "
        "application-specific stress combinations). Each corner is a dict "
        "of refdes -> multiplier."
    ),
)
def corner_analysis(
    components: _Components,
    corners: dict[str, dict[str, float]],
    spec: Annotated[dict[str, Any], Field(description=_FILTER_SPEC_DESC)],
    z0: Annotated[float, Field(gt=0)] = 50.0,
    transmission_zeros: bool | None = None,
    kind: Annotated[str, Field(description="lowpass, highpass, bandpass, or bandstop")] = "lowpass",
    topology: Annotated[str, Field(description="series_first or shunt_first")] = "series_first",
    component_substitution: Annotated[
        dict[str, dict[str, Any]] | None,
        Field(description="Optional output of substitute_real_components; includes parasitics."),
    ] = None,
) -> Envelope[dict[str, Any]]:
    components = _coerce_components(components)
    return _wrap(
        _corner_analysis,
        components,
        corners,
        spec,
        z0=z0,
        transmission_zeros=transmission_zeros,
        kind=kind,
        topology=topology,
        component_substitution=component_substitution,
    )


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Perturb each component by +/-pct and report the dB/% sensitivity of "
        "every spec criterion. Ranks components by total influence so you "
        "know which ones to grade tightly."
    ),
)
def sensitivity_analysis(
    components: _Components,
    spec: Annotated[dict[str, Any], Field(description=_FILTER_SPEC_DESC)],
    perturbation_pct: Annotated[float, Field(gt=0, le=10)] = 1.0,
    z0: Annotated[float, Field(gt=0)] = 50.0,
    transmission_zeros: bool | None = None,
    kind: Annotated[str, Field(description="lowpass, highpass, bandpass, or bandstop")] = "lowpass",
    topology: Annotated[str, Field(description="series_first or shunt_first")] = "series_first",
    component_substitution: Annotated[
        dict[str, dict[str, Any]] | None,
        Field(description="Optional output of substitute_real_components; includes parasitics."),
    ] = None,
) -> Envelope[dict[str, Any]]:
    components = _coerce_components(components)
    return _wrap(
        _sensitivity,
        components,
        spec,
        perturbation_pct=perturbation_pct,
        z0=z0,
        transmission_zeros=transmission_zeros,
        kind=kind,
        topology=topology,
        component_substitution=component_substitution,
    )


# ----- SRF audit -----------------------------------------------------------


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Flag inductors / capacitors whose self-resonant frequency is within "
        "margin_pct of the highest spec target. Above SRF the analytical "
        "model isn't predictive of real measurement."
    ),
)
def srf_audit(
    components: _Components,
    spec: Annotated[dict[str, Any], Field(description=_FILTER_SPEC_DESC)],
    inductor_vendor: str = "coilcraft_0402hp",
    capacitor_vendor: str = "murata_gjm_c0g",
    margin_pct: Annotated[float, Field(gt=0, le=100)] = 30.0,
) -> Envelope[dict[str, Any]]:
    components = _coerce_components(components)
    return _wrap(
        _srf_audit,
        components,
        spec,
        inductor_vendor=inductor_vendor,
        capacitor_vendor=capacitor_vendor,
        margin_pct=margin_pct,
    )


# ----- Analog active filters ----------------------------------------------


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Synthesize a Sallen-Key 2nd-order LPF (op-amp + 2R + 2C).",
)
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


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS, description="Synthesize a Sallen-Key 2nd-order HPF."
)
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


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Synthesize a Sallen-Key 2nd-order BPF (single op-amp).",
)
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


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Synthesize a Multiple-Feedback (MFB) 2nd-order LPF.",
)
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


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Synthesize a Multiple-Feedback (MFB) 2nd-order BPF.",
)
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Analyze an LDO at one operating point: efficiency, dropout, dissipation, output ripple.",
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


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Compute the PSRR (dB) an LDO needs to meet an output-ripple target.",
)
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


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Size a Buck (step-down) SMPS: L, Cout, ESR limit, peak/RMS currents.",
)
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


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Size a Boost (step-up) SMPS: L, Cout, ESR limit, peak/RMS currents.",
)
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


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Type-II compensator design (1 zero + 1 pole) for current-mode SMPS loops.",
)
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


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Compute crossover freq + phase margin from open-loop Bode arrays.",
)
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
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


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Setup/hold timing check on a synchronous digital path.",
)
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


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Estimate combinational propagation delay (gates + wires + fanout).",
)
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


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Estimate digital-to-analog crosstalk via mutual capacitance.",
)
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


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Estimate supply-rail droop from digital switching activity.",
)
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


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="List all op-amp part numbers in the bundled catalog.",
)
def list_opamps() -> Envelope[list[str]]:
    return _wrap(_list_opamps)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Look up an op-amp by part number (returns full datasheet params).",
)
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
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


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="List all MOSFET part numbers in the bundled catalog.",
)
def list_mosfets() -> Envelope[list[str]]:
    return _wrap(_list_mosfets)


@mcp.tool(annotations=DEFAULT_TOOL_ANNOTATIONS, description="Look up a MOSFET by part number.")
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


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Filter the MOSFET catalog by polarity, Vds, Id, Rds_on, Vgs threshold.",
)
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


@mcp.tool(annotations=DEFAULT_TOOL_ANNOTATIONS, description="List all BJT part numbers.")
def list_bjts() -> Envelope[list[str]]:
    return _wrap(_list_bjts)


@mcp.tool(annotations=DEFAULT_TOOL_ANNOTATIONS, description="Look up a BJT by part number.")
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


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="List all diode part numbers (signal / Schottky / TVS / zener / ESD).",
)
def list_diodes() -> Envelope[list[str]]:
    return _wrap(_list_diodes)


@mcp.tool(annotations=DEFAULT_TOOL_ANNOTATIONS, description="Look up a diode by part number.")
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


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS, description="List all voltage reference part numbers."
)
def list_references() -> Envelope[list[str]]:
    return _wrap(_list_refs)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS, description="Look up a voltage reference by part number."
)
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Run the synthesize -> place zeros -> vendor-snap -> optimize -> MC "
        "yield workflow for several filter orders side-by-side and return "
        "the most shippable. Default scoring favors all-pass + high yield + "
        "low SRF risk + few components."
    ),
)
def compare_filter_orders(
    orders: Annotated[list[int], Field(min_length=1, max_length=5)],
    cutoff_hz: Annotated[float, Field(gt=0)],
    spec: Annotated[dict[str, Any], Field(description=_FILTER_SPEC_DESC)],
    zero_targets_hz: list[float],
    ripple_db: Annotated[float, Field(gt=0)] = 0.1,
    stopband_atten_db: Annotated[float, Field(gt=0)] = 50.0,
    z0: Annotated[float, Field(gt=0)] = 50.0,
    inductor_vendor: str = "coilcraft_0402hp",
    capacitor_vendor: str = "murata_gjm_c0g",
    optimize_max_iter: Annotated[int, Field(gt=0, le=10000)] = 1500,
    passband_weight: Annotated[float, Field(gt=0)] = 30.0,
    mc_n_runs: Annotated[int, Field(gt=0, le=5_000)] = 1000,
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
                        "evaluation_mode": r.evaluation_mode,
                        "model_fidelity": r.model_fidelity,
                        "model_checksums": r.model_checksums,
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Render a clean publication-quality schematic of an LC ladder "
        "filter. Output format chosen from extension (.svg or .png)."
    ),
)
def render_lc_ladder_schematic(
    components: _Components,
    output_path: Annotated[str, Field(description="Output .svg or .png path.")],
    z0: Annotated[float, Field(gt=0)] = 50.0,
    transmission_zeros: bool = False,
    title: str | None = None,
) -> Envelope[dict[str, str]]:
    components = _coerce_components(components)
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    name="render_generated_lc_ladder_asc",
    description=(
        "Read component values from an LTspice .asc generated by this package "
        "and re-render its LC ladder as schemdraw SVG/PNG. Arbitrary LTspice "
        "symbols, wiring, hierarchy, and directives are not reconstructed."
    ),
)
def render_generated_lc_ladder_asc(
    asc_path: str,
    output_path: str,
    transmission_zeros: bool = True,
    title: str | None = None,
) -> Envelope[dict[str, str]]:
    timer = Timer()
    try:
        out = _render_generated_lc_ladder_asc(
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
            f"render_generated_lc_ladder_asc failed: {e}",
            tool_version=__version__,
        )


# Direct-Python compatibility for 0.x callers; not registered as an MCP tool.
render_asc_as_schematic = render_generated_lc_ladder_asc


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Run a real SPICE simulation on a schematic and reconcile it against "
        "the closed-form analytical S2P for the same components. Reports the "
        "per-region |S21| divergence and a verdict (agree / minor_disagreement "
        "/ disagree). Use this before trusting a reported yield or margin that "
        "came only from the fast analytical preview. If no simulator is "
        "installed it returns verdict='spice_unavailable' with the analytical "
        "response rather than failing. If `output_spice_s2p` is set, a second "
        "matched-port excitation is run to measure the full S-matrix honestly "
        "(same two-sweep method as `extract_sparameters`); the fast |S21| "
        "verdict above still comes from the single port-1 sweep."
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
    output_spice_s2p: Annotated[
        str | None,
        Field(
            description=(
                "If set, run a second matched-port excitation and write the "
                "measured S11/S21/S12/S22 two-port to this .s2p path (same "
                "two-sweep method as `extract_sparameters`; requires the "
                "V1+Rs1/RL1 port fixture)."
            )
        ),
    ] = None,
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

        spice_s2p_provenance: dict[str, Any] | None = None
        if output_spice_s2p and result.spice_network is not None:
            honest_net, spice_s2p_provenance = _build_two_sweep_spice_network(
                asc_path, result, z0=z0, timeout_sec=timeout_sec
            )
            write_touchstone(honest_net, output_spice_s2p)

        payload = _validation_payload(result, top_n_points=10)
        if output_analytical_s2p and result.analytical_network is not None:
            payload["analytical_s2p_path"] = str(output_analytical_s2p)
        if output_spice_s2p and result.spice_network is not None:
            payload["spice_s2p_path"] = str(output_spice_s2p)
            payload["spice_s2p_provenance"] = spice_s2p_provenance

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
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Index a directory of user-supplied vendor models (.s2p / .lib) so "
        "they appear as substitution candidates under a namespace. After "
        "registering, substitute_real_components(inductor_vendor='<namespace>', "
        "...) uses their reduced lumped estimates like any curated series. For "
        ".s2p files, kind, value, and SRF are recovered from a series-through "
        "fixture. .lib files are indexed by filename and assigned conservative "
        "default parasitics; their subcircuits are not instantiated. "
        "Re-registering refreshes the index. Per-file errors are non-fatal."
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
# Tool namespacing: register namespaced aliases alongside the flat names
# ---------------------------------------------------------------------------
#
# Categories help LLM agents discover tools by domain. Both the flat name
# (back-compat) and the namespaced alias work; over time the namespaced
# form is preferred. A future major release will deprecate the flat names.

NAMESPACE_ALIASES: dict[str, str] = {
    # filter.*: RF / lumped-LC filter design + analysis
    "synthesize_lc_filter": "filter.synthesize_lc",
    "synthesize_lc_hpf_filter": "filter.synthesize_lc_hpf",
    "synthesize_lc_bpf_filter": "filter.synthesize_lc_bpf",
    "synthesize_lc_bsf_filter": "filter.synthesize_lc_bsf",
    "place_transmission_zero": "filter.place_transmission_zero",
    "find_transmission_zeros": "filter.find_transmission_zeros",
    "evaluate_filter_spec": "filter.evaluate_spec",
    "render_response": "filter.render_response",
    "substitute_real_components": "filter.substitute_real_components",
    "simulate_realized_filter": "filter.simulate_realized",
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
    "render_generated_lc_ladder_asc": "filter.render_schematic",
    "build_design_report_pdf": "filter.build_report_pdf",
    # analog.*: active-filter / op-amp synthesis
    "sallen_key_low_pass": "analog.sallen_key_lpf",
    "sallen_key_high_pass": "analog.sallen_key_hpf",
    "sallen_key_band_pass": "analog.sallen_key_bpf",
    "mfb_low_pass": "analog.mfb_lpf",
    "mfb_band_pass": "analog.mfb_bpf",
    "cascaded_lpf_design": "analog.cascaded_lpf",
    # power.*: SMPS, LDO, control-loop analysis, EMC pre-compliance
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
    # digital.*: timing, crosstalk, supply-noise injection
    "check_setup_hold": "digital.check_setup_hold",
    "propagation_delay": "digital.propagation_delay",
    "estimate_digital_to_analog_crosstalk": "digital.digital_to_analog_xtalk",
    "estimate_supply_noise_injection": "digital.supply_noise_injection",
    # vendor.*: opamp / mosfet / bjt / diode / vref catalogues
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
    # sim.*: simulator runner / S-parameter extraction
    "run_simulation": "sim.run",
    "extract_sparameters": "sim.extract_sparameters",
}


def _register_namespaced_aliases() -> None:
    """Register dotted compatibility aliases for one deprecation window.

    Underscore-separated names are canonical because they are portable across
    MCP clients. Dotted aliases carry machine-readable removal metadata and
    will be removed in 1.0.
    """
    for flat_name, namespaced_name in NAMESPACE_ALIASES.items():
        implementation_name = (
            "evaluate_filter_spec_tool" if flat_name == "evaluate_filter_spec" else flat_name
        )
        func = globals().get(implementation_name)
        if func is None or not callable(func):
            continue
        mcp.tool(
            name=namespaced_name,
            annotations=DEFAULT_TOOL_ANNOTATIONS,
            description=(
                f"DEPRECATED compatibility alias of `{flat_name}`. Use "
                f"`{flat_name}`; this dotted alias will be removed in 1.0."
            ),
            meta={
                "deprecated": True,
                "canonical_name": flat_name,
                "remove_in": "1.0.0",
            },
        )(func)


_register_namespaced_aliases()
prepare_protocol_tools(mcp)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server on stdio."""
    log.info("starting mcp-ltspice", extra={"version": __version__})
    run_stdio_server(mcp)


if __name__ == "__main__":
    main()
