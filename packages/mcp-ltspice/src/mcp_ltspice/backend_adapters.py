"""ngspice and LTspice implementations of the shared backend contract."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import numpy as np

from mcp_ltspice.capabilities import probe_spice_backend
from mcp_ltspice.circuit_io import import_ltspice_asc
from mcp_ltspice.extract import _open_raw
from mcp_ltspice.runner import Simulator, run_simulation
from rf_mcp_common.backend import (
    AnalysisKind,
    BackendAdapterBase,
    BackendArtifact,
    BackendCapability,
    BackendName,
    BackendRunRequest,
    RawBackendResult,
    ResultAxis,
    ResultDataset,
    trace_from_array,
)
from rf_mcp_common.circuit_ir import CircuitAnalysis, CircuitDirective, CircuitDocument
from rf_mcp_common.simulation_workspace import sha256_file
from rf_mcp_common.spice_io import export_spice_text, parse_spice_file


def _analysis_statement(analysis: CircuitAnalysis) -> str:
    explicit = analysis.parameters.get("statement")
    if isinstance(explicit, str):
        return explicit
    if analysis.kind == "op":
        return ".op"
    if analysis.kind == "ac":
        sweep = str(analysis.parameters.get("sweep", "dec"))
        points = int(analysis.parameters.get("points", 100))
        start = analysis.parameters.get("f_start_hz")
        stop = analysis.parameters.get("f_stop_hz")
        if start is None or stop is None:
            raise ValueError("AC analysis requires f_start_hz and f_stop_hz")
        return f".ac {sweep} {points} {start} {stop}"
    if analysis.kind == "transient":
        step = analysis.parameters.get("time_step_s")
        stop = analysis.parameters.get("time_stop_s")
        if step is None or stop is None:
            raise ValueError("transient analysis requires time_step_s and time_stop_s")
        return f".tran {step} {stop}"
    raise ValueError(
        f"{analysis.kind} compilation requires an explicit dialect statement in parameters.statement"
    )


def _with_analysis(document: CircuitDocument, analysis: CircuitAnalysis) -> CircuitDocument:
    statement = _analysis_statement(analysis)
    directives = [
        directive
        for directive in document.directives
        if not directive.text.lower().startswith((".op", ".dc", ".ac", ".tran", ".noise", ".hb"))
    ]
    directives.append(CircuitDirective(text=statement, dialect=document.source_dialect))
    return document.model_copy(update={"directives": directives, "analyses": [analysis]}, deep=True)


def _capability(data: dict[str, Any], backend: BackendName) -> BackendCapability:
    supported: list[AnalysisKind] = []
    for item in data.get("supported_analyses", []):
        normalized = "sparameters" if item == "s_parameters" else item
        if normalized in {
            "op",
            "dc",
            "ac",
            "transient",
            "sparameters",
            "noise",
            "harmonic_balance",
        }:
            supported.append(normalized)  # type: ignore[arg-type]
    return BackendCapability(
        backend=backend,
        installed=bool(data.get("installed")),
        launchable=bool(data.get("launchable")),
        validated=bool(data.get("validated")),
        version=data.get("version"),
        supported_analyses=supported,
        diagnostic=data.get("diagnostic"),
        sandbox_profile=dict(data.get("sandbox_profile") or {}),
    )


class SpiceRawAdapter(BackendAdapterBase):
    """Common implementation for LTspice and ngspice raw-file workflows."""

    def __init__(self, backend: BackendName):
        if backend not in {"ngspice", "ltspice"}:
            raise ValueError("SpiceRawAdapter backend must be ngspice or ltspice")
        self.backend = backend

    def probe(self, *, validate: bool = False) -> BackendCapability:
        return _capability(probe_spice_backend(self.backend, validate=validate), self.backend)

    def import_file(self, path: str | Path) -> CircuitDocument:
        source = Path(path)
        if source.suffix.lower() == ".asc":
            return import_ltspice_asc(source)
        return parse_spice_file(
            source,
            dialect="ltspice" if self.backend == "ltspice" else "ngspice",
        )

    def compile(self, document: CircuitDocument, analysis: CircuitAnalysis) -> BackendArtifact:
        document.require_supported()
        if analysis.kind not in self.probe(validate=False).supported_analyses:
            raise ValueError(f"{self.backend} adapter does not support {analysis.kind}")
        compiled = _with_analysis(document, analysis)
        content = export_spice_text(
            compiled,
            dialect="ltspice" if self.backend == "ltspice" else "ngspice",
            preserve_source=False,
        )
        return BackendArtifact.from_text(
            backend=self.backend,
            filename=f"{document.document_id}.cir",
            media_type="application/x-spice-netlist",
            content=content,
            document=document,
            analysis=analysis,
            metadata={
                "dialect": self.backend,
                "model_hashes": {
                    component.refdes: component.model.checksum_sha256
                    for component in document.components
                    if component.model is not None
                },
                "model_sources": [
                    {
                        "refdes": component.refdes,
                        "path": component.model.source_path,
                        "sha256": component.model.checksum_sha256,
                    }
                    for component in document.components
                    if component.model is not None and component.model.source_path is not None
                ],
            },
        )

    def run(self, request: BackendRunRequest) -> RawBackendResult:
        if request.artifact.backend != self.backend:
            raise ValueError(f"artifact targets {request.artifact.backend}, not {self.backend}")
        request.workspace.mkdir(parents=True, exist_ok=True)
        netlist = request.workspace / request.artifact.filename
        content = request.artifact.content
        model_dir = request.workspace / "models"
        for source_record in request.artifact.metadata.get("model_sources", []):
            if not isinstance(source_record, dict):
                raise ValueError("invalid model source metadata")
            source_value = source_record.get("path")
            checksum = source_record.get("sha256")
            if not isinstance(source_value, str) or not isinstance(checksum, str):
                raise ValueError("model source metadata lacks path/checksum")
            source = Path(source_value).expanduser().resolve(strict=True)
            if sha256_file(source) != checksum:
                raise ValueError(f"model source checksum changed for {source_record.get('refdes')}")
            model_dir.mkdir(parents=True, exist_ok=True)
            staged_name = f"{checksum[:12]}-{source.name}"
            staged = model_dir / staged_name
            shutil.copy2(source, staged)
            content = content.replace(
                f'.include "{source_value}"',
                f'.include "models/{staged_name}"',
            )
        netlist.write_text(content, encoding="utf-8")
        result = run_simulation(
            netlist,
            prefer=Simulator(self.backend),
            timeout=request.timeout_sec,
            sandbox=request.sandbox,
        )
        return RawBackendResult(
            backend=self.backend,
            analysis=request.artifact.analysis.kind,
            artifact_paths=[result.raw_path, result.log_path],
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
            metadata={
                "raw_path": str(result.raw_path),
                "log_path": str(result.log_path),
                "sandboxed": result.sandboxed,
                "circuit_fingerprint": request.artifact.circuit_fingerprint,
                "input_sha256": request.artifact.content_sha256,
                "staged_input_sha256": sha256_file(netlist),
                "model_hashes": dict(request.artifact.metadata.get("model_hashes") or {}),
                "analysis_parameters": dict(request.artifact.analysis.parameters),
            },
        )

    def parse(self, raw: RawBackendResult) -> ResultDataset:
        if raw.backend != self.backend:
            raise ValueError(f"raw result belongs to {raw.backend}, not {self.backend}")
        path_value = raw.metadata.get("raw_path")
        if not isinstance(path_value, str):
            raise ValueError("raw result metadata has no raw_path")
        reader = _open_raw(path_value, dialect=self.backend)
        analysis_parameters = raw.metadata.get("analysis_parameters")
        parameters = analysis_parameters if isinstance(analysis_parameters, dict) else {}
        if raw.analysis in {"ac", "noise", "sparameters"}:
            axis_name, axis_unit = "frequency", "Hz"
        elif raw.analysis == "transient":
            axis_name, axis_unit = "time", "s"
        elif raw.analysis == "dc":
            axis_name = str(parameters.get("axis_name", "sweep"))
            axis_unit = str(parameters.get("axis_unit", "1"))
        else:
            axis_name, axis_unit = "index", "1"
        try:
            axis_trace = reader.get_trace(axis_name)
        except (IndexError, KeyError):
            axis_trace = None
        if axis_trace is None:
            axis_values = np.asarray(reader.get_axis(), dtype=np.complex128).real
        else:
            axis_values = np.asarray(axis_trace.get_wave(), dtype=np.complex128).real
        traces = {}
        for name in reader.get_trace_names():
            if name.lower() in {axis_name, "time", "frequency"}:
                continue
            trace = reader.get_trace(name)
            if trace is None:
                continue
            values = np.asarray(trace.get_wave())
            quantity, unit = (
                ("voltage", "V")
                if name.lower().startswith("v(")
                else ("current", "A")
                if name.lower().startswith("i(")
                else ("unknown", "1")
            )
            traces[name] = trace_from_array(name, values, unit=unit, quantity=quantity)
        return ResultDataset(
            backend=self.backend,
            backend_version=self.probe(validate=False).version,
            analysis=raw.analysis,
            axis=ResultAxis(
                name=axis_name,
                unit=axis_unit,
                values=axis_values.tolist(),
            ),
            traces=traces,
            method="simulator_raw_file",
            assumptions=[],
            provenance=dict(raw.metadata),
        )


class NgspiceAdapter(SpiceRawAdapter):
    def __init__(self) -> None:
        super().__init__("ngspice")


class LTspiceAdapter(SpiceRawAdapter):
    def __init__(self) -> None:
        super().__init__("ltspice")


__all__ = ["LTspiceAdapter", "NgspiceAdapter", "SpiceRawAdapter"]
