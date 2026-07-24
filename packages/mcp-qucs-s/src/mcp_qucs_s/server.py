"""FastMCP server entry point for mcp-qucs-s."""

from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from mcp_qucs_s import __version__
from mcp_qucs_s.backend_adapters import QucsatorAdapter, XyceAdapter
from mcp_qucs_s.capabilities import probe_qucs_backend as _probe_qucs_backend
from mcp_qucs_s.capabilities import qucs_capabilities as _qucs_capabilities
from mcp_qucs_s.circuit_io import export_qucs_file, import_qucs_file
from mcp_qucs_s.couplers import synthesize_coupler as _synthesize_coupler
from mcp_qucs_s.distributed import combline_bpf as _combline_bpf
from mcp_qucs_s.distributed import coupled_line_bpf as _coupled_line_bpf
from mcp_qucs_s.distributed import hairpin_bpf as _hairpin_bpf
from mcp_qucs_s.distributed import interdigital_bpf as _interdigital_bpf
from mcp_qucs_s.distributed import stepped_impedance_lpf as _stepped_impedance_lpf
from mcp_qucs_s.harmonic_balance import analyze as _hb_analyze
from mcp_qucs_s.harmonic_balance import sweep_compression as _hb_sweep_compression
from mcp_qucs_s.microstrip import (
    Substrate,
    analyze_microstrip,
)
from mcp_qucs_s.microstrip import (
    synthesize_microstrip_line as _synthesize_microstrip_line,
)
from mcp_qucs_s.netlist import generate_ladder_netlist as _generate_ladder_netlist
from mcp_qucs_s.noise import analyze_noise as _analyze_noise
from mcp_qucs_s.richards import lumped_to_distributed as _lumped_to_distributed
from mcp_qucs_s.runner import (
    is_qucs_available,
    is_xyce_available,
    run_qucs,
)
from mcp_qucs_s.sparams import dat_to_touchstone
from mcp_qucs_s.substrates import (
    get_substrate as _get_substrate_preset,
)
from mcp_qucs_s.substrates import (
    list_substrate_presets as _list_substrate_presets,
)
from rf_mcp_common.backend import BackendRunRequest
from rf_mcp_common.circuit_ir import CircuitAnalysis, CircuitDocument
from rf_mcp_common.envelope import Envelope, Timer, error, ok
from rf_mcp_common.jobs import DurableJobManager, JobContext, WorkspaceStore
from rf_mcp_common.logging import get_logger
from rf_mcp_common.protocol import prepare_protocol_tools, run_stdio_server
from rf_mcp_common.tool_annotations import DEFAULT_TOOL_ANNOTATIONS
from rf_mcp_common.tool_errors import EnvelopeErrorMiddleware

mcp = FastMCP(name="mcp-qucs-s", version=__version__)
mcp.add_middleware(EnvelopeErrorMiddleware())
log = get_logger("mcp_qucs_s.server")
_WORKSPACES = WorkspaceStore()
_JOBS = DurableJobManager(_WORKSPACES, max_workers=4)


def _circuit_from_payload(value: dict[str, Any]) -> CircuitDocument:
    """Accept either bare IR or the enriched circuit_parse response."""
    return CircuitDocument.model_validate(
        {key: item for key, item in value.items() if key in CircuitDocument.model_fields}
    )


def _parse_circuit_artifact(
    workspace_id: str,
    artifact_id: str,
    format: str | None = None,
) -> CircuitDocument:
    record = _WORKSPACES.artifact(workspace_id, artifact_id)
    return import_qucs_file(record["path"], format=format)  # type: ignore[arg-type]


def _select_adapter(kind: str) -> QucsatorAdapter | XyceAdapter:
    """Route a CircuitAnalysis kind to the backend that serves it.

    Qucsator and Xyce each serve a disjoint set of analyses, so the backend
    is always implied by ``kind`` rather than independently chosen.
    """
    if kind == "harmonic_balance":
        return XyceAdapter()
    if kind in {"sparameters", "noise"}:
        return QucsatorAdapter()
    raise ValueError(f"no qucs-s backend serves analysis kind {kind!r}")


def _simulation_job_handler(
    context: JobContext,
    payload: dict[str, Any],
) -> dict[str, Any]:
    workspace_id = str(payload["workspace_id"])
    record = _WORKSPACES.artifact(workspace_id, str(payload["artifact_id"]))
    analysis = CircuitAnalysis.model_validate(payload["analysis"])
    adapter = _select_adapter(analysis.kind)
    job_root = _JOBS.job_root(context.job_id)

    def progress(completed: int, total: int, message: str) -> None:
        context.update_progress(completed, total, message)

    progress(0, 4, "importing")
    if context.cancelled():
        raise RuntimeError("cancelled before compile")
    document = adapter.import_file(record["path"])
    progress(1, 4, "compiling")
    artifact = adapter.compile(document, analysis)
    if context.cancelled():
        raise RuntimeError("cancelled before run")
    progress(2, 4, "running")
    # Qucsator/Xyce have no verified OS sandbox profile (probe reports
    # sandbox_profile.available=False); these jobs run unsandboxed on the
    # immutable per-run workspace snapshot the adapter's `run` creates.
    raw = adapter.run(
        BackendRunRequest(
            artifact=artifact,
            workspace=job_root / "runs",
            timeout_sec=float(payload.get("timeout_sec", 120.0)),
            sandbox=False,
        )
    )
    progress(3, 4, "parsing")
    dataset = adapter.parse(raw)
    validation = adapter.validate(dataset, analysis)
    if not validation.valid:
        raise ValueError(f"backend result failed validation: {validation.model_dump()}")

    dataset_path = job_root / "result_dataset.json"
    dataset_path.write_text(
        json.dumps(dataset.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    netlist_path = job_root / artifact.filename
    netlist_path.write_text(artifact.content, encoding="utf-8")

    dataset_record = _WORKSPACES.add_generated(
        workspace_id, dataset_path, media_type="application/json"
    )
    netlist_record = _WORKSPACES.add_generated(
        workspace_id, netlist_path, media_type=artifact.media_type
    )
    output_artifact_id = None
    raw_output_path = raw.metadata.get("dataset_path") or raw.metadata.get("hb_path")
    if raw_output_path:
        output_record = _WORKSPACES.add_generated(
            workspace_id, raw_output_path, media_type="application/octet-stream"
        )
        output_artifact_id = output_record["artifact_id"]
    progress(4, 4, "completed")

    return {
        "backend": adapter.backend,
        "analysis": analysis.kind,
        "returncode": raw.returncode,
        "validation": validation.model_dump(mode="json"),
        "dataset_artifact_id": dataset_record["artifact_id"],
        "dataset_resource_uri": f"artifact://{workspace_id}/{dataset_record['artifact_id']}",
        "netlist_artifact_id": netlist_record["artifact_id"],
        "output_artifact_id": output_artifact_id,
        "run_manifest_path": raw.metadata.get("manifest_path"),
        "circuit_fingerprint": artifact.circuit_fingerprint,
        "input_sha256": artifact.content_sha256,
    }


_JOBS.register("simulation", _simulation_job_handler)


@mcp.resource("capabilities://mcp-qucs-s")
def capabilities_resource() -> dict[str, Any]:
    return _qucs_capabilities()


@mcp.resource("artifact://{workspace_id}/{artifact_id}")
def artifact_resource(workspace_id: str, artifact_id: str) -> bytes:
    """Read a checksum-verified Qucs workspace artifact."""
    return _WORKSPACES.read(workspace_id, artifact_id)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Create a durable server-owned workspace for Qucs circuit artifacts.",
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
    description="Import a Qucs schematic or netlist into a confined workspace.",
)
def artifact_import(
    workspace_id: str,
    source_path: str,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        record = _WORKSPACES.import_file(
            workspace_id,
            source_path,
            media_type="application/x-qucs",
        )
        return ok(
            record | {"resource_uri": (f"artifact://{workspace_id}/{record['artifact_id']}")},
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as exc:
        return error(f"artifact_import failed: {exc}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Parse an imported Qucs schematic or Qucsator netlist into versioned "
        "CircuitDocument IR. Unknown component pin geometry is a blocking, "
        "source-located diagnostic."
    ),
)
def circuit_parse(
    workspace_id: str,
    artifact_id: str,
    format: Annotated[
        str | None,
        Field(description="'schematic', 'netlist', or infer from .sch suffix."),
    ] = None,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        document = _parse_circuit_artifact(workspace_id, artifact_id, format)
        payload = document.model_dump(mode="json")
        payload.update(
            {
                "source_artifact_id": artifact_id,
                "is_supported": document.is_supported,
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
        "Export a supported CircuitDocument to Qucsator netlist or its preserved "
        "Qucs schematic and return a checksum-addressed artifact."
    ),
)
def circuit_export(
    workspace_id: str,
    circuit: dict[str, Any],
    output_format: Annotated[str, Field(description="'netlist' or 'schematic'")],
    name: str | None = None,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        if output_format not in {"netlist", "schematic"}:
            raise ValueError("output_format must be 'netlist' or 'schematic'")
        document = _circuit_from_payload(circuit)
        document.require_supported()
        suffix = ".net" if output_format == "netlist" else ".sch"
        safe_name = Path(name or f"{document.document_id}{suffix}").name
        if Path(safe_name).suffix.lower() != suffix:
            safe_name += suffix
        with tempfile.TemporaryDirectory(prefix="rf-mcp-qucs-export-") as temporary:
            target = Path(temporary) / safe_name
            export_qucs_file(
                document,
                target,
                format=output_format,  # type: ignore[arg-type]
            )
            record = _WORKSPACES.add_generated(
                workspace_id,
                target,
                name=safe_name,
                media_type="application/x-qucs",
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
        "Administrative readiness probe for qucsator or Xyce. Distinguishes "
        "installed, launchable, and known-answer validated states."
    ),
)
def probe_backend(
    backend: Annotated[str, Field(description="'qucsator' or 'xyce'")],
    validate: bool = True,
    timeout_sec: Annotated[float, Field(gt=0, le=120)] = 20.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        return ok(
            _probe_qucs_backend(
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
    description=(
        "Submit a durable, cancellable circuit simulation job: compile the "
        "imported artifact, run it, parse the result into a normalized "
        "ResultDataset, and validate it against the analysis. `analysis.kind` "
        "selects the backend — Qucsator serves 'sparameters'/'noise', Xyce "
        "serves 'harmonic_balance'. Qucsator and Xyce have no verified OS "
        "sandbox profile, so jobs run unsandboxed on the immutable per-run "
        "workspace snapshot; only use this on trusted local inputs."
    ),
)
def simulation_submit(
    workspace_id: str,
    artifact_id: str,
    analysis: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "CircuitAnalysis {id, kind, parameters}; kind is 'sparameters', "
                "'noise', or 'harmonic_balance'. Defaults to the artifact's "
                "first parsed analysis."
            )
        ),
    ] = None,
    timeout_sec: Annotated[float, Field(gt=0, le=600)] = 120.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        record = _WORKSPACES.artifact(workspace_id, artifact_id)
        resolved_analysis = analysis
        if resolved_analysis is None:
            document = import_qucs_file(record["path"])
            if not document.analyses:
                raise ValueError("artifact has no parsed analysis; pass an explicit `analysis` IR")
            resolved_analysis = document.analyses[0].model_dump(mode="json")
        parsed_analysis = CircuitAnalysis.model_validate(resolved_analysis)
        _select_adapter(parsed_analysis.kind)  # fail fast on an unroutable kind
        job = _JOBS.submit(
            "simulation",
            {
                "workspace_id": workspace_id,
                "artifact_id": artifact_id,
                "analysis": resolved_analysis,
                "timeout_sec": timeout_sec,
            },
            workspace_id=workspace_id,
        )
        return ok(job, runtime_sec=timer.elapsed(), tool_version=__version__)
    except Exception as exc:
        return error(f"simulation_submit failed: {exc}", tool_version=__version__)


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


def _substrate(d: dict[str, float] | str) -> Substrate:
    """Coerce either a preset-name string or a parameter dict into a Substrate.

    String inputs look up `mcp_qucs_s.substrates.SUBSTRATE_PRESETS`. Dict
    inputs require `er` and `h_mm` keys; `t_um` and `tan_d` default to
    35 µm and 0.02 if absent.
    """
    if isinstance(d, str):
        return _get_substrate_preset(d)
    return Substrate(
        er=d["er"],
        h_mm=d["h_mm"],
        t_um=d.get("t_um", 35.0),
        tan_d=d.get("tan_d", 0.02),
    )


# ---------------------------------------------------------------------------
# Status / capability discovery
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "List curated substrate presets (FR4, Rogers RO4350B / RO4003C, "
        "Duroid 5880 / 6002, PTFE, Isola FR408HR, Taconic TLY5) with their "
        "{er, h_mm, t_um, tan_d} values. Pass a preset name as the `substrate` "
        "argument to `synthesize_microstrip_line` and `analyze_microstrip_tool` "
        "instead of the full dict."
    ),
)
def list_substrate_presets_tool() -> Envelope[list[dict[str, Any]]]:
    timer = Timer()
    try:
        return ok(
            _list_substrate_presets(),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"list_substrate_presets failed: {e}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Report synthesis features and backend readiness states. Use "
        "probe_backend(validate=true) for a fresh known-answer validation."
    ),
)
def status() -> Envelope[dict[str, Any]]:
    return ok(
        {
            "version": __version__,
            "qucs_s_available": is_qucs_available(),
            "xyce_available": is_xyce_available(),
            "backends": _qucs_capabilities(),
            "synthesis_tools": [
                "synthesize_microstrip_line",
                "analyze_microstrip",
                "synthesize_coupler",
                "lumped_to_distributed",
                "synthesize_stepped_impedance_lpf",
                "synthesize_coupled_line_bpf",
                "synthesize_hairpin_bpf",
                "synthesize_interdigital_bpf",
                "synthesize_combline_bpf",
            ],
            "sim_tools_requiring_qucs_s": [
                "run_sp_analysis",
                "extract_noise_parameters",
                "export_touchstone",
            ],
            "sim_tools_requiring_xyce": ["run_harmonic_balance", "sweep_compression_point"],
        },
        tool_version=__version__,
    )


# ---------------------------------------------------------------------------
# Tools 3-5: Microstrip + coupler synthesis (no simulator needed)
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Synthesize microstrip line dimensions for a target characteristic "
        "impedance and electrical length. Hammerstad-Jensen closed form. "
        "NOTE: this is the **synthesis** direction (Z₀, length, freq → W, L). "
        "For impedance **analysis** of an existing trace from PCB geometry, "
        "prefer a PCB-layout-aware EMC MCP if one is available."
    ),
)
def synthesize_microstrip_line(
    z0_ohm: Annotated[float, Field(gt=0)],
    electrical_length_deg: Annotated[float, Field(ge=0, le=720)],
    freq_hz: Annotated[float, Field(gt=0)],
    substrate: Annotated[
        dict[str, float] | str,
        Field(
            description=(
                "Either a preset name (e.g. 'FR4_0254', 'Rogers4350B_0508', "
                "'Duroid5880_0508' — see `list_substrate_presets_tool`) OR a "
                "parameter dict {er, h_mm, t_um (default 35), tan_d (default 0.02)}."
            )
        ),
    ],
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        sub = _substrate(substrate)
        line = _synthesize_microstrip_line(z0_ohm, electrical_length_deg, freq_hz, sub)
        return ok(
            {
                "z0_ohm": line.z0,
                "width_mm": line.width_mm,
                "length_mm": line.length_mm,
                "eff_permittivity": line.eff_permittivity,
                "wavelength_eff_mm": line.wavelength_eff_mm,
                "metadata": line.metadata,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"synthesize_microstrip_line failed: {e}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Analyze an existing microstrip line: Z0, eps_eff, wavelength. "
        "Hammerstad-Jensen closed form. NOTE: for PCB impedance analysis from "
        "stackup + trace data, prefer a PCB-layout-aware EMC MCP if one is "
        "available — those tools integrate with the wider PCB analysis "
        "workflow (CPW, stripline, differential, eye-diagram)."
    ),
)
def analyze_microstrip_tool(
    width_mm: Annotated[float, Field(gt=0)],
    substrate: dict[str, float] | str,
    freq_hz: Annotated[float, Field(gt=0)] = 1e9,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        return ok(
            analyze_microstrip(width_mm, _substrate(substrate), freq_hz),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"analyze_microstrip failed: {e}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Synthesize a directional coupler: branch_line / rat_race / "
        "coupled_line / lange. Returns per-section dimensions."
    ),
)
def synthesize_coupler(
    kind: Annotated[str, Field(description="branch_line | rat_race | coupled_line | lange")],
    coupling_db: Annotated[float, Field(gt=0, le=30)],
    freq_hz: Annotated[float, Field(gt=0)],
    z0_ohm: Annotated[float, Field(gt=0)],
    substrate: dict[str, float],
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        sub = _substrate(substrate)
        result = _synthesize_coupler(kind, coupling_db, freq_hz, z0_ohm, sub)  # type: ignore[arg-type]
        return ok(
            {
                "kind": result.kind,
                "coupling_db": result.coupling_db,
                "freq_hz": result.freq_hz,
                "z0_ohm": result.z0,
                "sections": result.sections,
                "notes": result.notes,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"synthesize_coupler failed: {e}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Convert a lumped LC ladder to its distributed-element microstrip "
        "equivalent via Richards transformation + Kuroda identities."
    ),
)
def lumped_to_distributed(
    components: dict[str, float],
    cutoff_hz: Annotated[float, Field(gt=0)],
    substrate: dict[str, float],
    z0_ohm: Annotated[float, Field(gt=0)] = 50.0,
    apply_kuroda: bool = True,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        sub = _substrate(substrate)
        result = _lumped_to_distributed(
            components,
            cutoff_hz,
            z0=z0_ohm,
            substrate=sub,
            apply_kuroda=apply_kuroda,
        )
        return ok(result, runtime_sec=timer.elapsed(), tool_version=__version__)
    except Exception as e:
        return error(f"lumped_to_distributed failed: {e}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Synthesize a stepped-impedance microstrip LPF (Pozar §8.6) from a "
        "lumped LPF ladder: series inductors become short high-Z sections "
        "(βl = ω_c·L/Z_h), shunt capacitors short low-Z sections "
        "(βl = ω_c·C·Z_l). Returns per-section impedance, electrical length "
        "at cutoff, and microstrip width/length on the given substrate. "
        "Sections exceeding the βl < 45° approximation are flagged in notes. "
        "Simulate the result with simulate_lc_ladder-style netlists via "
        "generate_microstrip_ladder_netlist (real MLIN model) or ideal "
        "series_tline elements."
    ),
)
def synthesize_stepped_impedance_lpf(
    components: dict[str, float],
    cutoff_hz: Annotated[float, Field(gt=0)],
    substrate: dict[str, float] | str,
    z0_ohm: Annotated[float, Field(gt=0)] = 50.0,
    z_high_ohm: Annotated[float, Field(gt=0, description="High-Z section impedance.")] = 120.0,
    z_low_ohm: Annotated[float, Field(gt=0, description="Low-Z section impedance.")] = 20.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        sub = _substrate(substrate)
        result = _stepped_impedance_lpf(
            components,
            cutoff_hz,
            z0=z0_ohm,
            z_high=z_high_ohm,
            z_low=z_low_ohm,
            substrate=sub,
        )
        return ok(result, runtime_sec=timer.elapsed(), tool_version=__version__)
    except Exception as e:
        return error(f"synthesize_stepped_impedance_lpf failed: {e}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Synthesize an edge-coupled (parallel coupled-line) microstrip BPF "
        "(Pozar §8.7) from LPF prototype g-coefficients (pass the "
        "g_coefficients list a lumped synthesis tool returns). Order N → N+1 "
        "quarter-wave coupled sections via J-inverters; each section gets "
        "even/odd impedances plus physical (W, S, L) from quasi-static "
        "Garg-Bahl coupled-microstrip inversion. This is also the electrical "
        "core of the hairpin filter (hairpin = same sections, resonators "
        "folded). Unrealizable couplings (bandwidth too wide for edge-coupled "
        "geometry) are rejected with an explanation."
    ),
)
def synthesize_coupled_line_bpf(
    g_coefficients: list[float],
    f0_hz: Annotated[float, Field(gt=0, description="Passband centre frequency.")],
    fractional_bandwidth: Annotated[float, Field(gt=0, lt=1)],
    substrate: dict[str, float] | str,
    z0_ohm: Annotated[float, Field(gt=0)] = 50.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        sub = _substrate(substrate)
        result = _coupled_line_bpf(
            g_coefficients,
            f0_hz,
            fractional_bandwidth,
            z0=z0_ohm,
            substrate=sub,
        )
        return ok(result, runtime_sec=timer.elapsed(), tool_version=__version__)
    except Exception as e:
        return error(f"synthesize_coupled_line_bpf failed: {e}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Synthesize a hairpin microstrip BPF (folded edge-coupled / "
        "Cristal-Frankel hairpin-line) from LPF prototype g-coefficients. "
        "Each half-wave resonator is folded into a U; the bend connector's "
        "electrical length is compensated by shortening every coupled section "
        "to 90° − θ_bend/2 so resonators stay at exactly 180° at f0. Returns "
        "the coupled sections (W, S, L), the per-resonator arm/bend table, "
        "and honest notes on what is not modeled (corner discontinuities, "
        "cross-arm self-coupling)."
    ),
)
def synthesize_hairpin_bpf(
    g_coefficients: list[float],
    f0_hz: Annotated[float, Field(gt=0, description="Passband centre frequency.")],
    fractional_bandwidth: Annotated[float, Field(gt=0, lt=1)],
    substrate: dict[str, float] | str,
    z0_ohm: Annotated[float, Field(gt=0)] = 50.0,
    bend_mm: Annotated[
        float | None,
        Field(description="U-bend connector length; default 3× the mean arm width."),
    ] = None,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        sub = _substrate(substrate)
        result = _hairpin_bpf(
            g_coefficients,
            f0_hz,
            fractional_bandwidth,
            z0=z0_ohm,
            substrate=sub,
            bend_mm=bend_mm,
        )
        return ok(result, runtime_sec=timer.elapsed(), tool_version=__version__)
    except Exception as e:
        return error(f"synthesize_hairpin_bpf failed: {e}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Synthesize an interdigital microstrip BPF from LPF prototype "
        "g-coefficients: N coupled λ/4 resonators, alternately shorted, "
        "tapped I/O. Couplings k = Δ/√(g_i·g_j) are realised exactly on the "
        "same-velocity TEM array model (closed-form pair-resonance split); "
        "the tap point comes from the shorted-λ/4 slope parameter. Returns "
        "resonator/coupling tables with per-pair (W, S) plus the exact array "
        "description (y_c, segments, terminations) for simulation. "
        "Unrealizable Δ/Z_resonator combinations are rejected."
    ),
)
def synthesize_interdigital_bpf(
    g_coefficients: list[float],
    f0_hz: Annotated[float, Field(gt=0, description="Passband centre frequency.")],
    fractional_bandwidth: Annotated[float, Field(gt=0, lt=1)],
    substrate: dict[str, float] | str,
    z0_ohm: Annotated[float, Field(gt=0)] = 50.0,
    z_resonator_ohm: Annotated[float, Field(gt=0)] = 70.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        sub = _substrate(substrate)
        result = _interdigital_bpf(
            g_coefficients,
            f0_hz,
            fractional_bandwidth,
            z0=z0_ohm,
            substrate=sub,
            z_resonator_ohm=z_resonator_ohm,
        )
        result = dict(result)
        result["y_c"] = [[float(v) for v in row] for row in result["y_c"]]
        return ok(result, runtime_sec=timer.elapsed(), tool_version=__version__)
    except Exception as e:
        return error(f"synthesize_interdigital_bpf failed: {e}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Synthesize a combline microstrip BPF from LPF prototype "
        "g-coefficients: N coupled lines shorted at the same end, each tuned "
        "by a lumped capacitor at the open end, resonator length θ0 (default "
        "45°). Couplings solved on the exact TEM pair transcendental "
        "ωC = (Y_r ± y_m)·cotθ; tap from the loaded-resonator slope "
        "parameter b = (Y_r/2)(cotθ0 + θ0·csc²θ0). Clean upper stopband to "
        "≈ (180/θ0)·f0. Returns resonator (incl. c_load_farad) and coupling "
        "tables, the exact array description, and self-reported 'achieved' "
        "response metrics. Unrealizable Δ/Z_resonator combinations rejected."
    ),
)
def synthesize_combline_bpf(
    g_coefficients: list[float],
    f0_hz: Annotated[float, Field(gt=0, description="Passband centre frequency.")],
    fractional_bandwidth: Annotated[float, Field(gt=0, lt=1)],
    substrate: dict[str, float] | str,
    z0_ohm: Annotated[float, Field(gt=0)] = 50.0,
    z_resonator_ohm: Annotated[float, Field(gt=0)] = 70.0,
    theta0_deg: Annotated[float, Field(ge=10, lt=90)] = 45.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        sub = _substrate(substrate)
        result = _combline_bpf(
            g_coefficients,
            f0_hz,
            fractional_bandwidth,
            z0=z0_ohm,
            substrate=sub,
            z_resonator_ohm=z_resonator_ohm,
            theta0_deg=theta0_deg,
        )
        result = dict(result)
        result["y_c"] = [[float(v) for v in row] for row in result["y_c"]]
        return ok(result, runtime_sec=timer.elapsed(), tool_version=__version__)
    except Exception as e:
        return error(f"synthesize_combline_bpf failed: {e}", tool_version=__version__)


# ---------------------------------------------------------------------------
# Tools 1, 6, 8: Simulator-driven (need Qucs-S installed)
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Run native Qucs-S S-parameter analysis on a qucsator netlist "
        "(generate one with simulate_lc_ladder, or hand-write it; this is "
        "the netlist format, not the GUI .sch file). "
        "Requires Qucs-S installed (see docs/installation.md). Output is "
        "a Touchstone .s2p."
    ),
)
def run_sp_analysis(
    netlist_path: str,
    output_s2p: str,
    timeout_sec: Annotated[float, Field(gt=0, le=600)] = 300.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        if not is_qucs_available():
            return error(
                "Qucs-S not installed. See docs/installation.md to build "
                "from source: github.com/ra3xdh/qucs_s",
                tool_version=__version__,
            )
        result = run_qucs(netlist_path, timeout_sec=timeout_sec)
        s2p = dat_to_touchstone(result.output_path, output_s2p)
        return ok(
            {
                "s2p_path": str(s2p),
                "dat_path": str(result.output_path),
                "run_manifest_path": str(result.manifest_path),
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"run_sp_analysis failed: {e}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Design-to-Touchstone in one call: build a Qucs netlist for a lumped "
        "LC ladder, simulate it with qucsator, and write S-parameters as a "
        ".s2p file. Each element states its position explicitly, e.g. "
        "{'kind': 'series_l', 'L': 7.96e-9} or {'kind': 'shunt_c', 'C': 6.37e-12}. "
        "Kinds: series_l, series_c, shunt_l, shunt_c, shunt_lc_trap, "
        "shunt_lc_parallel, series_lc_series, series_lc_parallel. LC kinds take "
        "both L and C. Elements are ordered source to load."
    ),
)
def simulate_lc_ladder(
    elements: Annotated[
        list[dict[str, Any]],
        Field(description="Ordered source-to-load elements, each with 'kind' plus L and/or C."),
    ],
    output_s2p: Annotated[str, Field(description="Path for the output .s2p file.")],
    z0: Annotated[float, Field(gt=0)] = 50.0,
    f_start_hz: Annotated[float, Field(gt=0)] = 1e6,
    f_stop_hz: Annotated[float, Field(gt=0)] = 5e9,
    points: Annotated[int, Field(ge=2, le=100_000)] = 200,
    netlist_path: Annotated[
        str | None,
        Field(description="Where to keep the generated netlist. Default: beside the .s2p."),
    ] = None,
    timeout_sec: Annotated[float, Field(gt=0, le=600)] = 300.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        if not is_qucs_available():
            return error(
                "Qucs-S not installed. See docs/installation.md to build "
                "from source: github.com/ra3xdh/qucs_s",
                tool_version=__version__,
            )

        parsed: list[tuple[str, dict[str, float]]] = []
        for i, raw in enumerate(elements, start=1):
            if "kind" not in raw:
                return error(
                    f"Element {i} has no 'kind'. Each element needs a kind plus "
                    "its L and/or C value, e.g. {'kind': 'shunt_c', 'C': 6.37e-12}.",
                    tool_version=__version__,
                )
            params = {k: float(v) for k, v in raw.items() if k in ("L", "C")}
            parsed.append((str(raw["kind"]), params))

        s2p_path = Path(output_s2p).expanduser().resolve()
        net_path = (
            Path(netlist_path).expanduser().resolve()
            if netlist_path
            else s2p_path.with_suffix(".net")
        )
        net = _generate_ladder_netlist(
            parsed,
            net_path,
            z0=z0,
            f_start_hz=f_start_hz,
            f_stop_hz=f_stop_hz,
            points=points,
        )
        result = run_qucs(net, timeout_sec=timeout_sec)
        s2p = dat_to_touchstone(result.output_path, s2p_path, z0=z0)
        return ok(
            {
                "s2p_path": str(s2p),
                "netlist_path": str(net),
                "dat_path": str(result.output_path),
                "run_manifest_path": str(result.manifest_path),
                "n_elements": len(parsed),
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"simulate_lc_ladder failed: {e}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Run harmonic-balance analysis via the Xyce backend. Returns the "
        "spectral content at all mixing products. Requires Xyce installed."
    ),
)
def run_harmonic_balance(
    dut_netlist: Annotated[
        list[str],
        Field(
            description=(
                "Raw SPICE lines for the circuit under test — devices, .SUBCKT "
                "and .MODEL cards — referring to the in_node and out_node names. "
                "Do not include sources, termination or analysis directives; "
                "those are added here. Use explicit multiplication in B-source "
                "expressions (V(in)*V(in)*V(in)); the '^' operator makes Xyce's "
                "HB startup transient diverge."
            )
        ),
    ],
    fundamentals_hz: Annotated[
        list[float],
        Field(description="One tone for harmonic distortion, two for intermod."),
    ],
    harmonics: Annotated[int, Field(ge=1, le=32)] = 5,
    input_power_dbm: float = -20.0,
    in_node: str = "in",
    out_node: str = "out",
    z0: Annotated[float, Field(gt=0)] = 50.0,
    timeout_sec: Annotated[float, Field(gt=0, le=600)] = 300.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        if not is_xyce_available():
            return error(
                "Xyce not installed. Build it from source — see "
                "docs/installation.md. Sandia's Linux binaries are RHEL RPMs "
                "that do not run on Debian/Ubuntu, and they no longer ship "
                "open-source builds.",
                tool_version=__version__,
            )

        result = _hb_analyze(
            dut_netlist,
            fundamentals_hz=fundamentals_hz,
            harmonics=harmonics,
            input_power_dbm=input_power_dbm,
            in_node=in_node,
            out_node=out_node,
            z0=z0,
            timeout_sec=timeout_sec,
        )

        payload: dict[str, Any] = {
            "fundamentals_hz": result.fundamentals_hz,
            "fundamental_dbm": result.fundamental_dbm,
            "input_power_dbm": result.input_power_dbm,
            "gain_db": result.gain_db,
            "spectrum": result.spectrum.top(20),
            "netlist_path": str(result.netlist_path),
            "output_path": str(result.output_path),
            "run_manifest_path": (
                str(result.manifest_path) if result.manifest_path is not None else None
            ),
        }
        env_warnings: list[str] = []
        if result.im3_dbm is not None:
            payload |= {
                "im3_dbm": result.im3_dbm,
                "im3_freqs_hz": result.im3_freqs_hz,
                "oip3_dbm": result.oip3_dbm,
                "iip3_dbm": result.iip3_dbm,
            }
            # The single-point extrapolation assumes the products still sit on
            # their 3:1 slope. Near compression they do not, and IIP3 reads low.
            if result.gain_db is not None and result.fundamental_dbm:
                env_warnings.append(
                    "IIP3 is extrapolated from one drive level, which assumes the "
                    "third-order products are still on their 3:1 slope. Confirm by "
                    "re-running a few dB lower and checking IIP3 does not move."
                )

        env: Envelope[dict[str, Any]] = ok(
            payload, runtime_sec=timer.elapsed(), tool_version=__version__
        )
        env.warnings.extend(env_warnings)
        return env
    except Exception as e:
        return error(f"run_harmonic_balance failed: {e}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Sweep drive level through harmonic balance and locate the 1 dB "
        "gain-compression point (P1dB). Requires Xyce. The lowest power swept "
        "sets the small-signal gain reference, so keep it well below compression."
    ),
)
def sweep_compression_point(
    dut_netlist: list[str],
    fundamental_hz: float,
    input_powers_dbm: list[float],
    harmonics: Annotated[int, Field(ge=1, le=32)] = 5,
    in_node: str = "in",
    out_node: str = "out",
    z0: Annotated[float, Field(gt=0)] = 50.0,
    timeout_sec: Annotated[float, Field(gt=0, le=600)] = 300.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        if not is_xyce_available():
            return error(
                "Xyce not installed. See docs/installation.md for the source build.",
                tool_version=__version__,
            )
        data = _hb_sweep_compression(
            dut_netlist,
            fundamental_hz=fundamental_hz,
            input_powers_dbm=input_powers_dbm,
            harmonics=harmonics,
            in_node=in_node,
            out_node=out_node,
            z0=z0,
            timeout_sec=timeout_sec,
        )
        env: Envelope[dict[str, Any]] = ok(
            data, runtime_sec=timer.elapsed(), tool_version=__version__
        )
        if data.get("p1db_in_dbm") is None:
            env.warnings.append(
                "Gain never compressed by 1 dB across the swept range, so P1dB is "
                "not bracketed. Extend the sweep to higher drive levels."
            )
        return env
    except Exception as e:
        return error(f"sweep_compression_point failed: {e}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description="Run Qucs-S sim and export S-parameters to Touchstone in one call.",
)
def export_touchstone(
    netlist_path: str,
    output_s2p: str,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        if not is_qucs_available():
            return error(
                "Qucs-S not installed. See docs/installation.md.",
                tool_version=__version__,
            )
        result = run_qucs(netlist_path)
        s2p = dat_to_touchstone(result.output_path, output_s2p)
        return ok(
            {
                "s2p_path": str(Path(s2p).resolve()),
                "run_manifest_path": str(result.manifest_path),
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"export_touchstone failed: {e}", tool_version=__version__)


@mcp.tool(
    annotations=DEFAULT_TOOL_ANNOTATIONS,
    description=(
        "Run a Qucs-S noise analysis and return the four classical noise "
        "parameters per frequency: NF50 (dB), Fmin (dB), Gamma_opt "
        "(magnitude and angle) and Rn (ohms). Optionally also evaluates the "
        "noise figure at a given source reflection coefficient, which is what "
        "an LNA input match actually presents. Requires Qucs-S."
    ),
)
def extract_noise_parameters(
    dut_netlist: Annotated[
        list[str],
        Field(
            description=(
                "Qucs netlist lines for the circuit under test, referring to "
                "nodes _p1 and _p2 and using gnd for ground. Ports and the .SP "
                "analysis are added here. Example: "
                "['R:R1 _p1 _p2 R=\"20\"', 'R:R2 _p2 gnd R=\"100\"']"
            )
        ),
    ],
    f_start_hz: Annotated[float, Field(gt=0)],
    f_stop_hz: Annotated[float, Field(gt=0)],
    points: Annotated[int, Field(ge=1, le=10_000)] = 21,
    z0: Annotated[float, Field(gt=0)] = 50.0,
    temp_c: Annotated[
        float | None,
        Field(
            description=(
                "Noise temperature in Celsius applied to resistor lines lacking "
                "an explicit Temp. Defaults to the IEEE reference 16.85 C (290 K), "
                "at which a passive network's noise figure equals its loss. Qucs "
                "itself defaults components to 26.85 C. Pass null to leave the "
                "netlist untouched."
            )
        ),
    ] = 16.85,
    source_gamma_real: float | None = None,
    source_gamma_imag: float | None = None,
    timeout_sec: Annotated[float, Field(gt=0, le=600)] = 300.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        if not is_qucs_available():
            return error(
                "Qucs-S not installed. See docs/installation.md to build "
                "from source: github.com/ra3xdh/qucs_s",
                tool_version=__version__,
            )

        params = _analyze_noise(
            dut_netlist,
            f_start_hz=f_start_hz,
            f_stop_hz=f_stop_hz,
            points=points,
            z0=z0,
            temp_c=temp_c,
            timeout_sec=timeout_sec,
        )

        payload: dict[str, Any] = {
            "z0": z0,
            "temp_c": temp_c,
            "n_points": int(params.freq_hz.size),
            "parameters": params.as_rows(),
        }
        if source_gamma_real is not None or source_gamma_imag is not None:
            gamma_s = complex(source_gamma_real or 0.0, source_gamma_imag or 0.0)
            payload["source_gamma"] = {"real": gamma_s.real, "imag": gamma_s.imag}
            payload["nf_db_at_source"] = [float(v) for v in params.nf_db_at_source(gamma_s)]

        return ok(payload, runtime_sec=timer.elapsed(), tool_version=__version__)
    except Exception as e:
        return error(f"extract_noise_parameters failed: {e}", tool_version=__version__)


def main() -> None:
    log.info("starting mcp-qucs-s", extra={"version": __version__})
    run_stdio_server(mcp)


prepare_protocol_tools(mcp)


if __name__ == "__main__":
    main()
