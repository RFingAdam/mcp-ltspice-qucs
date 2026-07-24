"""Qucsator and Xyce implementations of the shared backend contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from mcp_qucs_s.capabilities import probe_qucs_backend
from mcp_qucs_s.circuit_io import (
    export_qucs_netlist_text,
    import_qucs_file,
)
from mcp_qucs_s.harmonic_balance import parse_hb_fd, run_xyce
from mcp_qucs_s.runner import run_qucs
from mcp_qucs_s.sparams import parse_qucs_dat
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
from rf_mcp_common.circuit_ir import (
    CircuitAnalysis,
    CircuitComponent,
    CircuitDirective,
    CircuitDocument,
)
from rf_mcp_common.spice_io import export_spice_text, parse_spice_file

_QUCS_MODEL_BY_KIND = {
    "resistor": ("R", "R"),
    "capacitor": ("C", "C"),
    "inductor": ("L", "L"),
    "voltage_source": ("Vac", "U"),
    "current_source": ("Iac", "I"),
    "power_port": ("Pac", None),
    "transmission_line": ("TLIN", None),
    "microstrip_line": ("MLIN", None),
    "coupled_transmission_line": ("CTLIN", None),
}


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


def _qucs_analysis_line(analysis: CircuitAnalysis) -> str:
    explicit = analysis.parameters.get("statement")
    if isinstance(explicit, str):
        return explicit
    if analysis.kind == "sparameters":
        sweep = str(analysis.parameters.get("sweep", "log"))
        start = analysis.parameters.get("f_start_hz")
        stop = analysis.parameters.get("f_stop_hz")
        points = int(analysis.parameters.get("points", 201))
        if start is None or stop is None:
            raise ValueError("S-parameter analysis requires f_start_hz and f_stop_hz")
        return f'.SP:SP1 Type="{sweep}" Start="{start}" Stop="{stop}" Points="{points}"'
    raise ValueError(f"{analysis.kind} Qucs compilation requires parameters.statement")


def _as_qucs_document(document: CircuitDocument, analysis: CircuitAnalysis) -> CircuitDocument:
    if document.source_format == "qucs_netlist":
        directives = [item for item in document.directives if not item.text.startswith(".")]
        directives.append(CircuitDirective(text=_qucs_analysis_line(analysis), dialect="qucs"))
        return document.model_copy(
            update={"directives": directives, "analyses": [analysis]},
            deep=True,
        )
    components: list[CircuitComponent] = []
    for component in document.components:
        if component.model is not None:
            raise ValueError(
                f"{component.refdes}: Qucsator generic compilation does not yet "
                "support attached component models; use ngspice/LTspice/Xyce or "
                "supply a native Qucs model"
            )
        mapping = _QUCS_MODEL_BY_KIND.get(component.kind)
        if mapping is None:
            raise ValueError(
                f"{component.refdes}: kind {component.kind!r} cannot be compiled for Qucsator"
            )
        model, _ = mapping
        components.append(
            component.model_copy(
                update={"attributes": {**component.attributes, "qucs_model": model}},
                deep=True,
            )
        )
    existing_power_ports = {item.refdes for item in components if item.kind == "power_port"}
    for port in document.ports:
        refdes = f"P{port.number or len(existing_power_ports) + 1}"
        if refdes in existing_power_ports:
            continue
        components.append(
            CircuitComponent(
                refdes=refdes,
                kind="power_port",
                pins={"1": port.positive_net, "2": port.negative_net},
                parameters={
                    "Num": port.number or len(existing_power_ports) + 1,
                    "Z": f"{port.impedance_ohm or 50.0} Ohm",
                    "P": "0 dBm",
                    "f": "1 GHz",
                },
                attributes={"qucs_model": "Pac"},
            )
        )
        existing_power_ports.add(refdes)
    return CircuitDocument(
        document_id=document.document_id,
        title=document.title,
        source_format="generated",
        source_dialect="qucsator",
        parameters=dict(document.parameters),
        nodes=document.nodes,
        components=components,
        ports=document.ports,
        analyses=[analysis],
        directives=[CircuitDirective(text=_qucs_analysis_line(analysis), dialect="qucs")],
        dependencies=document.dependencies,
        provenance=document.provenance,
        metadata={},
    )


class QucsatorAdapter(BackendAdapterBase):
    backend: BackendName = "qucsator"

    def probe(self, *, validate: bool = False) -> BackendCapability:
        return _capability(probe_qucs_backend("qucsator", validate=validate), self.backend)

    def import_file(self, path: str | Path) -> CircuitDocument:
        return import_qucs_file(path)

    def compile(self, document: CircuitDocument, analysis: CircuitAnalysis) -> BackendArtifact:
        document.require_supported()
        if analysis.kind not in self.probe(validate=False).supported_analyses:
            raise ValueError(f"qucsator adapter does not support {analysis.kind}")
        qucs_document = _as_qucs_document(document, analysis)
        content = export_qucs_netlist_text(qucs_document, preserve_source=False)
        return BackendArtifact.from_text(
            backend=self.backend,
            filename=f"{document.document_id}.net",
            media_type="application/x-qucs-netlist",
            content=content,
            document=document,
            analysis=analysis,
        )

    def run(self, request: BackendRunRequest) -> RawBackendResult:
        if request.artifact.backend != self.backend:
            raise ValueError("artifact does not target qucsator")
        if request.sandbox:
            raise RuntimeError(
                "Qucsator has no verified OS sandbox profile; "
                "set sandbox=false only for trusted local inputs"
            )
        request.workspace.mkdir(parents=True, exist_ok=True)
        path = request.workspace / request.artifact.filename
        path.write_text(request.artifact.content, encoding="utf-8")
        result = run_qucs(
            path,
            output_path=request.workspace / "result.dat",
            timeout_sec=request.timeout_sec,
            workspace_root=request.workspace / "runs",
        )
        return RawBackendResult(
            backend=self.backend,
            analysis=request.artifact.analysis.kind,
            artifact_paths=[result.output_path, result.log_path, result.manifest_path],
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
            metadata={
                "dataset_path": str(result.output_path),
                "manifest_path": str(result.manifest_path),
                "circuit_fingerprint": request.artifact.circuit_fingerprint,
                "input_sha256": request.artifact.content_sha256,
            },
        )

    def parse(self, raw: RawBackendResult) -> ResultDataset:
        path_value = raw.metadata.get("dataset_path")
        if not isinstance(path_value, str):
            raise ValueError("raw Qucsator result has no dataset_path")
        data = parse_qucs_dat(path_value)
        if "frequency" not in data:
            raise ValueError("Qucsator dataset has no frequency axis")
        axis = np.asarray(data.pop("frequency"), dtype=float)
        traces = {}
        consumed: set[str] = set()
        for name, values in sorted(data.items()):
            if name in consumed:
                continue
            if name.endswith(".r") and f"{name[:-2]}.i" in data:
                base = name[:-2]
                array = np.asarray(values, dtype=float) + 1j * np.asarray(
                    data[f"{base}.i"], dtype=float
                )
                consumed.update({name, f"{base}.i"})
                trace_name = base
            elif name.endswith(".i") and f"{name[:-2]}.r" in data:
                continue
            else:
                array = np.asarray(values)
                trace_name = name
            quantity = "sparameter" if trace_name.startswith("S[") else "unknown"
            traces[trace_name] = trace_from_array(trace_name, array, unit="1", quantity=quantity)
        return ResultDataset(
            backend=self.backend,
            backend_version=self.probe(validate=False).version,
            analysis=raw.analysis,
            axis=ResultAxis(name="frequency", unit="Hz", values=axis.tolist()),
            traces=traces,
            method="qucsator_dataset",
            provenance=dict(raw.metadata),
        )


class XyceAdapter(BackendAdapterBase):
    backend: BackendName = "xyce"

    def probe(self, *, validate: bool = False) -> BackendCapability:
        return _capability(probe_qucs_backend("xyce", validate=validate), self.backend)

    def import_file(self, path: str | Path) -> CircuitDocument:
        return parse_spice_file(path, dialect="xyce")

    def compile(self, document: CircuitDocument, analysis: CircuitAnalysis) -> BackendArtifact:
        document.require_supported()
        if analysis.kind != "harmonic_balance":
            raise ValueError("Xyce adapter currently supports harmonic_balance")
        statement = analysis.parameters.get("statement")
        if not isinstance(statement, str):
            raise ValueError("Xyce harmonic balance requires parameters.statement")
        directives = [
            item
            for item in document.directives
            if not item.text.lower().startswith((".hb", ".print"))
        ]
        directives.append(CircuitDirective(text=statement, dialect="xyce"))
        print_statement = analysis.parameters.get("print_statement")
        if isinstance(print_statement, str):
            directives.append(CircuitDirective(text=print_statement, dialect="xyce"))
        compiled = document.model_copy(
            update={"directives": directives, "analyses": [analysis]}, deep=True
        )
        content = export_spice_text(compiled, dialect="xyce", preserve_source=False)
        return BackendArtifact.from_text(
            backend=self.backend,
            filename=f"{document.document_id}.cir",
            media_type="application/x-spice-netlist",
            content=content,
            document=document,
            analysis=analysis,
            metadata={
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
            raise ValueError("artifact does not target Xyce")
        if request.sandbox:
            raise RuntimeError(
                "Xyce has no verified OS sandbox profile; "
                "set sandbox=false only for trusted local inputs"
            )
        model_sources: list[tuple[Path, str]] = []
        for source_record in request.artifact.metadata.get("model_sources", []):
            if not isinstance(source_record, dict):
                raise ValueError("invalid model source metadata")
            source_value = source_record.get("path")
            checksum = source_record.get("sha256")
            if not isinstance(source_value, str) or not isinstance(checksum, str):
                raise ValueError("model source metadata lacks path/checksum")
            model_sources.append((Path(source_value), checksum))
        path = run_xyce(
            request.artifact.content,
            workdir=request.workspace,
            timeout_sec=request.timeout_sec,
            model_sources=model_sources,
        )
        return RawBackendResult(
            backend=self.backend,
            analysis="harmonic_balance",
            artifact_paths=[path],
            returncode=0,
            metadata={
                "hb_path": str(path),
                "circuit_fingerprint": request.artifact.circuit_fingerprint,
                "input_sha256": request.artifact.content_sha256,
                "model_hashes": dict(request.artifact.metadata.get("model_hashes") or {}),
            },
        )

    def parse(self, raw: RawBackendResult) -> ResultDataset:
        path_value = raw.metadata.get("hb_path")
        if not isinstance(path_value, str):
            raise ValueError("raw Xyce result has no hb_path")
        spectrum = parse_hb_fd(path_value)
        return ResultDataset(
            backend=self.backend,
            backend_version=self.probe(validate=False).version,
            analysis="harmonic_balance",
            axis=ResultAxis(name="frequency", unit="Hz", values=spectrum.freqs_hz.tolist()),
            traces={
                "V(out)": trace_from_array(
                    "V(out)", spectrum.volts_peak, unit="V", quantity="voltage_peak"
                ),
                "P(out)": trace_from_array("P(out)", spectrum.dbm, unit="dBm", quantity="power"),
            },
            method="xyce_harmonic_balance_frequency_domain",
            assumptions=["positive-frequency amplitudes are doubled; DC is not"],
            provenance=dict(raw.metadata),
        )


__all__ = ["QucsatorAdapter", "XyceAdapter"]
