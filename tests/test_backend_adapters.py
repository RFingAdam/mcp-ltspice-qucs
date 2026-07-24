from __future__ import annotations

from pathlib import Path

import pytest

from mcp_ltspice.backend_adapters import LTspiceAdapter, NgspiceAdapter
from mcp_qucs_s.backend_adapters import QucsatorAdapter, XyceAdapter
from rf_mcp_common.backend import BackendAdapter, BackendRunRequest
from rf_mcp_common.circuit_ir import (
    CircuitAnalysis,
    CircuitComponent,
    CircuitDocument,
    CircuitNode,
    CircuitPort,
    ModelReference,
)


def _document() -> CircuitDocument:
    return CircuitDocument(
        document_id="adapter-fixture",
        source_format="generated",
        nodes=[
            CircuitNode(id="0", is_ground=True),
            CircuitNode(id="in"),
            CircuitNode(id="out"),
        ],
        components=[
            CircuitComponent(
                refdes="R1",
                kind="resistor",
                pins={"1": "in", "2": "out"},
                value="50",
            ),
            CircuitComponent(
                refdes="R2",
                kind="resistor",
                pins={"1": "out", "2": "0"},
                value="50",
            ),
        ],
        ports=[
            CircuitPort(
                name="P1",
                number=1,
                positive_net="in",
                negative_net="0",
                impedance_ohm=50,
            ),
            CircuitPort(
                name="P2",
                number=2,
                positive_net="out",
                negative_net="0",
                impedance_ohm=50,
            ),
        ],
    )


def test_all_adapters_implement_shared_runtime_contract() -> None:
    adapters = [NgspiceAdapter(), LTspiceAdapter(), QucsatorAdapter(), XyceAdapter()]
    assert all(isinstance(adapter, BackendAdapter) for adapter in adapters)
    assert {adapter.backend for adapter in adapters} == {
        "ngspice",
        "ltspice",
        "qucsator",
        "xyce",
    }


def test_spice_adapters_compile_same_ir_with_analysis() -> None:
    document = _document()
    analysis = CircuitAnalysis(
        id="ac1",
        kind="ac",
        parameters={
            "sweep": "dec",
            "points": 10,
            "f_start_hz": 1e3,
            "f_stop_hz": 1e6,
        },
    )
    ngspice = NgspiceAdapter().compile(document, analysis)
    ltspice = LTspiceAdapter().compile(document, analysis)
    assert ngspice.circuit_fingerprint == ltspice.circuit_fingerprint
    assert ".ac dec 10 1000.0 1000000.0" in ngspice.content
    assert "R1 in out 50" in ngspice.content


def test_qucsator_adapter_compiles_ports_and_sparameter_analysis() -> None:
    artifact = QucsatorAdapter().compile(
        _document(),
        CircuitAnalysis(
            id="sp1",
            kind="sparameters",
            parameters={
                "sweep": "log",
                "points": 101,
                "f_start_hz": 1e6,
                "f_stop_hz": 1e9,
            },
        ),
    )
    assert "Pac:P1 in gnd" in artifact.content
    assert "Pac:P2 out gnd" in artifact.content
    assert ".SP:SP1" in artifact.content


def test_xyce_adapter_compiles_explicit_harmonic_balance_statement() -> None:
    artifact = XyceAdapter().compile(
        _document(),
        CircuitAnalysis(
            id="hb1",
            kind="harmonic_balance",
            parameters={
                "statement": ".HB 1e6",
                "print_statement": ".PRINT HB_FD V(out)",
            },
        ),
    )
    assert ".HB 1e6" in artifact.content
    assert ".PRINT HB_FD V(out)" in artifact.content


def test_unverified_qucs_and_xyce_sandboxes_are_not_silently_ignored(
    tmp_path: Path,
) -> None:
    qucs = QucsatorAdapter()
    qucs_artifact = qucs.compile(
        _document(),
        CircuitAnalysis(
            id="sp1",
            kind="sparameters",
            parameters={"f_start_hz": 1e6, "f_stop_hz": 1e9},
        ),
    )
    with pytest.raises(RuntimeError, match="no verified OS sandbox"):
        qucs.run(BackendRunRequest(artifact=qucs_artifact, workspace=tmp_path / "qucs"))

    xyce = XyceAdapter()
    xyce_artifact = xyce.compile(
        _document(),
        CircuitAnalysis(
            id="hb1",
            kind="harmonic_balance",
            parameters={"statement": ".HB 1e6"},
        ),
    )
    with pytest.raises(RuntimeError, match="no verified OS sandbox"):
        xyce.run(BackendRunRequest(artifact=xyce_artifact, workspace=tmp_path / "xyce"))


def test_xyce_artifact_attests_selected_model_sources(tmp_path: Path) -> None:
    library = tmp_path / "part.lib"
    library.write_text(".subckt part p n\nR1 p n 50\n.ends part\n", encoding="utf-8")
    document = _document()
    document.components[0].model = ModelReference(
        provider="fixture",
        checksum_sha256="a" * 64,
        source_reference=str(library),
        model_kind="subckt",
        pin_map={"positive": 1, "negative": 2},
        source_path=str(library),
        subcircuit_name="part",
    )
    artifact = XyceAdapter().compile(
        document,
        CircuitAnalysis(
            id="hb1",
            kind="harmonic_balance",
            parameters={"statement": ".HB 1e6"},
        ),
    )
    assert artifact.metadata["model_hashes"] == {"R1": "a" * 64}
    assert artifact.metadata["model_sources"][0]["path"] == str(library)
