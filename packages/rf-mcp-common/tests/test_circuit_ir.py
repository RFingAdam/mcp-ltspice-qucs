from __future__ import annotations

import pytest

from rf_mcp_common.circuit_ir import (
    CIRCUIT_SCHEMA_VERSION,
    CircuitChange,
    CircuitComponent,
    CircuitDocument,
    CircuitNode,
    UnsupportedConstruct,
)


def _document() -> CircuitDocument:
    return CircuitDocument(
        document_id="divider",
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
                value="1k",
            ),
            CircuitComponent(
                refdes="R2",
                kind="resistor",
                pins={"1": "out", "2": "0"},
                value="1k",
            ),
        ],
    )


def test_versioned_document_and_topology_preserving_transform() -> None:
    original = _document()
    changed = original.transformed(
        [
            CircuitChange(
                path="components.R2.value",
                before="1k",
                after="2k",
                reason="set divider ratio",
            )
        ],
        operation="unit-test",
    )

    assert original.schema_version == CIRCUIT_SCHEMA_VERSION == "1.0"
    assert changed.components[1].value == "2k"
    assert original.components[1].value == "1k"
    assert changed.connectivity_signature() == original.connectivity_signature()
    assert changed.electrical_fingerprint() != original.electrical_fingerprint()
    assert changed.provenance.transformations[0]["operation"] == "unit-test"


def test_document_rejects_dangling_connectivity() -> None:
    with pytest.raises(ValueError, match="unknown nets"):
        CircuitDocument(
            document_id="bad",
            source_format="generated",
            nodes=[CircuitNode(id="0", is_ground=True)],
            components=[
                CircuitComponent(
                    refdes="R1",
                    kind="resistor",
                    pins={"1": "missing", "2": "0"},
                    value=1.0,
                )
            ],
        )


def test_require_supported_lists_all_blocking_diagnostics() -> None:
    document = _document().model_copy(
        update={
            "unsupported": [
                UnsupportedConstruct(code="one", message="first"),
                UnsupportedConstruct(code="two", message="second"),
                UnsupportedConstruct(code="note", message="non-blocking", severity="warning"),
            ]
        }
    )
    with pytest.raises(ValueError, match=r"one: first; two: second"):
        document.require_supported()
