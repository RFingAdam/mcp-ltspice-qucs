from __future__ import annotations

import pytest

from mcp_ltspice.backend_adapters import NgspiceAdapter
from mcp_ltspice.vendor_models import (
    ComponentModel,
    attach_component_models,
    component_model_for_part,
    lookup_part,
)
from rf_mcp_common.circuit_ir import (
    CircuitAnalysis,
    CircuitComponent,
    CircuitDocument,
    CircuitNode,
)


def _inductor_document() -> CircuitDocument:
    return CircuitDocument(
        document_id="modeled-inductor",
        source_format="generated",
        nodes=[CircuitNode(id="0", is_ground=True), CircuitNode(id="in")],
        components=[
            CircuitComponent(
                refdes="L1",
                kind="inductor",
                pins={"1": "in", "2": "0"},
                value=4.7e-9,
            )
        ],
    )


def _ac() -> CircuitAnalysis:
    return CircuitAnalysis(
        id="ac1",
        kind="ac",
        parameters={
            "sweep": "dec",
            "points": 10,
            "f_start_hz": 1e6,
            "f_stop_hz": 1e9,
        },
    )


def test_curated_lumped_model_is_instantiated_and_hashed() -> None:
    part = lookup_part("coilcraft_0402hp", 4.7e-9, kind="L")
    model = component_model_for_part("coilcraft_0402hp", part, kind="L")
    document = attach_component_models(_inductor_document(), {"L1": model})
    artifact = NgspiceAdapter().compile(document, _ac())

    assert ".SUBCKT RFMC_L1 p n" in artifact.content
    assert "Rloss p x" in artifact.content
    assert "Lmain x n" in artifact.content
    assert "Cpar p n" in artifact.content
    assert "XL1 in 0 RFMC_L1" in artifact.content
    assert artifact.metadata["model_hashes"] == {"L1": model.checksum_sha256}
    assert document.provenance.transformations[-1]["operation"] == "attach-component-models"


def test_generic_lumped_model_uses_tuned_component_value() -> None:
    part = lookup_part("coilcraft_0402hp", 4.7e-9, kind="L")
    model = component_model_for_part("coilcraft_0402hp", part, kind="L")
    source = _inductor_document()
    source.components[0].value = 8.2e-9
    document = attach_component_models(source, {"L1": model})

    artifact = NgspiceAdapter().compile(document, _ac())
    assert "Lmain x n 8.2e-09" in artifact.content


def test_subcircuit_model_emits_exact_include_and_instance(tmp_path) -> None:
    library = tmp_path / "part.lib"
    library.write_text(".subckt exact_part p n\nR1 p n 1\n.ends exact_part\n", encoding="utf-8")
    model = ComponentModel(
        provider="fixture",
        source_reference=str(library),
        manufacturer_part_number="EXACT-1",
        model_kind="subckt",
        pin_map={"positive": 1, "negative": 2},
        valid_frequency_hz=(0, 1e9),
        valid_bias={},
        valid_temperature_c=(-40, 125),
        checksum_sha256="c" * 64,
        source_path=str(library),
        subcircuit_name="exact_part",
        record_kind="orderable_part",
        orderable=True,
        availability="in_stock",
    )
    source = _inductor_document()
    source.components[0].parameters["area"] = 2
    document = attach_component_models(source, {"L1": model})
    artifact = NgspiceAdapter().compile(document, _ac())

    assert f'.include "{library}"' in artifact.content
    assert "XL1 in 0 exact_part area=2" in artifact.content
    assert document.dependencies[0].checksum_sha256 == "c" * 64


def test_subcircuit_model_honors_explicit_logical_pin_map(tmp_path) -> None:
    library = tmp_path / "reversed.lib"
    library.write_text(".subckt exact_part n p\nR1 p n 1\n.ends exact_part\n", encoding="utf-8")
    model = ComponentModel(
        provider="fixture",
        source_reference=str(library),
        manufacturer_part_number=None,
        model_kind="subckt",
        pin_map={"positive": 2, "negative": 1},
        valid_frequency_hz=(0, 1e9),
        valid_bias={},
        valid_temperature_c=(-40, 125),
        checksum_sha256="d" * 64,
        source_path=str(library),
        subcircuit_name="exact_part",
    )
    document = _inductor_document()
    document.components[0].pins = {"positive": "in", "negative": "0"}
    attached = attach_component_models(document, {"L1": model})

    artifact = NgspiceAdapter().compile(attached, _ac())
    assert "XL1 0 in exact_part" in artifact.content


def test_passive_model_kind_mismatch_is_rejected() -> None:
    capacitor = lookup_part("murata_gjm_c0g", 4.7e-12, kind="C")
    model = component_model_for_part("murata_gjm_c0g", capacitor, kind="C")
    with pytest.raises(ValueError, match="cannot use a F component model"):
        attach_component_models(_inductor_document(), {"L1": model})
