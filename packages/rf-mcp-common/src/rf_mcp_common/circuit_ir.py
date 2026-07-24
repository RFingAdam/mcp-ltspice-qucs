"""Versioned, simulator-independent circuit intermediate representation.

The IR deliberately separates source syntax from electrical meaning.  Importers
must either populate explicit pin-to-net connectivity or attach an
``UnsupportedConstruct`` diagnostic; they must never guess topology from a
reference designator or drawing order.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CIRCUIT_SCHEMA_VERSION: Literal["1.0"] = "1.0"
SourceFormat = Literal["spice", "ltspice_asc", "qucs_netlist", "qucs_schematic", "generated"]


class SourceLocation(BaseModel):
    """Location of a construct in its source artifact."""

    model_config = ConfigDict(extra="forbid")

    artifact: str | None = None
    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)


class UnsupportedConstruct(BaseModel):
    """A source construct whose electrical semantics were not imported."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    raw: str | None = None
    location: SourceLocation | None = None
    severity: Literal["warning", "error"] = "error"


class CircuitNode(BaseModel):
    """One electrical net."""

    model_config = ConfigDict(extra="forbid")

    id: str
    aliases: list[str] = Field(default_factory=list)
    is_ground: bool = False


class CircuitGeometry(BaseModel):
    """Optional source-format drawing information."""

    model_config = ConfigDict(extra="allow")

    x: float | None = None
    y: float | None = None
    rotation_deg: int | None = None
    mirrored: bool = False
    source_token: str | None = None


class ModelReference(BaseModel):
    """Immutable component-model identity used by a circuit instance."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    checksum_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    source_reference: str
    model_kind: Literal["subckt", "model", "touchstone", "lumped_approximation"]
    pin_map: dict[str, int] = Field(default_factory=dict)
    manufacturer_part_number: str | None = None
    source_path: str | None = None
    subcircuit_name: str | None = None
    electrical_parameters: dict[str, float] = Field(default_factory=dict)
    nominal_value: float | None = None
    nominal_unit: Literal["H", "F", "Ohm"] | None = None
    valid_frequency_hz: tuple[float | None, float | None] = (None, None)
    valid_bias: dict[str, float | None] = Field(default_factory=dict)
    valid_temperature_c: tuple[float | None, float | None] = (None, None)
    record_kind: Literal["orderable_part", "technology_model"] = "technology_model"
    orderable: bool = False
    package: str | None = None
    model_license: str | None = None


class CircuitComponent(BaseModel):
    """A primitive or modeled circuit instance with explicit connectivity."""

    model_config = ConfigDict(extra="allow")

    refdes: str
    kind: str
    pins: dict[str, str]
    value: str | float | None = None
    parameters: dict[str, str | float | int | bool] = Field(default_factory=dict)
    model: ModelReference | None = None
    geometry: CircuitGeometry | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    source: SourceLocation | None = None

    @model_validator(mode="after")
    def _require_pins(self) -> CircuitComponent:
        if not self.pins:
            raise ValueError(f"component {self.refdes!r} has no pins")
        if any(not name or not net for name, net in self.pins.items()):
            raise ValueError(f"component {self.refdes!r} has an empty pin or net name")
        return self


class CircuitPort(BaseModel):
    """External circuit port or terminal fixture."""

    model_config = ConfigDict(extra="forbid")

    name: str
    positive_net: str
    negative_net: str = "0"
    impedance_ohm: float | None = Field(default=None, gt=0)
    number: int | None = Field(default=None, ge=1)


class CircuitAnalysis(BaseModel):
    """Requested analysis without binding it to a simulator dialect."""

    model_config = ConfigDict(extra="allow")

    id: str
    kind: Literal["op", "dc", "ac", "transient", "sparameters", "noise", "harmonic_balance"]
    parameters: dict[str, str | float | int | bool] = Field(default_factory=dict)


class CircuitDirective(BaseModel):
    """A preserved source directive."""

    model_config = ConfigDict(extra="forbid")

    text: str
    dialect: str | None = None
    source: SourceLocation | None = None


class CircuitDependency(BaseModel):
    """A model/include dependency referenced by the source circuit."""

    model_config = ConfigDict(extra="forbid")

    reference: str
    kind: Literal["include", "library", "model", "subcircuit", "touchstone"]
    checksum_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    resolved_artifact_id: str | None = None


class CircuitProvenance(BaseModel):
    """Source and transformation history for an IR document."""

    model_config = ConfigDict(extra="allow")

    source_artifact: str | None = None
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    importer: str | None = None
    importer_version: str | None = None
    transformations: list[dict[str, Any]] = Field(default_factory=list)


class CircuitChange(BaseModel):
    """One topology-preserving document mutation."""

    model_config = ConfigDict(extra="forbid")

    path: str
    before: Any
    after: Any
    reason: str | None = None


class CircuitDocument(BaseModel):
    """Portable circuit document shared by every backend.

    ``unsupported`` is part of the contract rather than an incidental warning
    list.  A caller may inspect partially imported documents, but
    :meth:`require_supported` must be called before compiling or simulating.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = CIRCUIT_SCHEMA_VERSION
    document_id: str
    title: str | None = None
    source_format: SourceFormat
    source_dialect: str | None = None
    parameters: dict[str, str | float | int | bool] = Field(default_factory=dict)
    nodes: list[CircuitNode]
    components: list[CircuitComponent]
    ports: list[CircuitPort] = Field(default_factory=list)
    analyses: list[CircuitAnalysis] = Field(default_factory=list)
    directives: list[CircuitDirective] = Field(default_factory=list)
    dependencies: list[CircuitDependency] = Field(default_factory=list)
    provenance: CircuitProvenance = Field(default_factory=CircuitProvenance)
    unsupported: list[UnsupportedConstruct] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_graph(self) -> CircuitDocument:
        node_ids = [node.id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("node ids must be unique")
        refdes = [component.refdes for component in self.components]
        if len(refdes) != len(set(refdes)):
            raise ValueError("component reference designators must be unique")
        known = set(node_ids)
        dangling = sorted(
            {
                net
                for component in self.components
                for net in component.pins.values()
                if net not in known
            }
            | {
                net
                for port in self.ports
                for net in (port.positive_net, port.negative_net)
                if net not in known
            }
        )
        if dangling:
            raise ValueError(f"pins/ports reference unknown nets: {', '.join(dangling)}")
        return self

    @property
    def is_supported(self) -> bool:
        return not any(item.severity == "error" for item in self.unsupported)

    def require_supported(self) -> None:
        """Raise with every blocking diagnostic before compilation."""
        errors = [item for item in self.unsupported if item.severity == "error"]
        if errors:
            details = "; ".join(
                f"{item.code}"
                + (f" at line {item.location.line}" if item.location and item.location.line else "")
                + f": {item.message}"
                for item in errors
            )
            raise ValueError(f"circuit has unsupported constructs: {details}")

    def connectivity_signature(self) -> tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...]:
        """Stable electrical-connectivity signature for round-trip tests."""
        return tuple(
            sorted(
                (
                    component.refdes,
                    component.kind,
                    tuple(sorted(component.pins.items())),
                )
                for component in self.components
            )
        )

    def electrical_fingerprint(self) -> str:
        """SHA-256 over the backend-independent electrical content."""
        payload = {
            "schema_version": self.schema_version,
            "nodes": [node.model_dump(mode="json") for node in self.nodes],
            "components": [component.model_dump(mode="json") for component in self.components],
            "ports": [port.model_dump(mode="json") for port in self.ports],
            "analyses": [analysis.model_dump(mode="json") for analysis in self.analyses],
            "directives": [directive.model_dump(mode="json") for directive in self.directives],
            "dependencies": [
                dependency.model_dump(mode="json") for dependency in self.dependencies
            ],
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def transformed(self, changes: list[CircuitChange], *, operation: str) -> CircuitDocument:
        """Apply value/parameter changes to a deep copy and retain an audit trail.

        Supported paths are ``components.<refdes>.value`` and
        ``components.<refdes>.parameters.<name>``.  Connectivity is intentionally
        immutable through this helper.
        """
        result = copy.deepcopy(self)
        by_ref = {component.refdes: component for component in result.components}
        for change in changes:
            parts = change.path.split(".")
            if len(parts) not in (3, 4) or parts[0] != "components":
                raise ValueError(f"unsupported change path {change.path!r}")
            component = by_ref.get(parts[1])
            if component is None:
                raise ValueError(f"unknown component {parts[1]!r}")
            if parts[2] == "value" and len(parts) == 3:
                actual = component.value
                if actual != change.before:
                    raise ValueError(
                        f"stale change for {change.path}: expected {change.before!r}, found {actual!r}"
                    )
                component.value = change.after
            elif parts[2] == "parameters" and len(parts) == 4:
                actual = component.parameters.get(parts[3])
                if actual != change.before:
                    raise ValueError(
                        f"stale change for {change.path}: expected {change.before!r}, found {actual!r}"
                    )
                component.parameters[parts[3]] = change.after
            else:
                raise ValueError(f"unsupported change path {change.path!r}")
        result.provenance.transformations.append(
            {
                "operation": operation,
                "changes": [change.model_dump(mode="json") for change in changes],
                "input_fingerprint": self.electrical_fingerprint(),
            }
        )
        return result


def node_list(names: set[str] | list[str]) -> list[CircuitNode]:
    """Create deterministically ordered nodes with SPICE/Qucs ground aliases."""
    unique = set(names)
    return [
        CircuitNode(
            id=name,
            aliases=["gnd"] if name == "0" else [],
            is_ground=name in {"0", "gnd", "GND"},
        )
        for name in sorted(unique, key=lambda value: (value not in {"0", "gnd", "GND"}, value))
    ]


__all__ = [
    "CIRCUIT_SCHEMA_VERSION",
    "CircuitAnalysis",
    "CircuitChange",
    "CircuitComponent",
    "CircuitDependency",
    "CircuitDirective",
    "CircuitDocument",
    "CircuitGeometry",
    "CircuitNode",
    "CircuitPort",
    "CircuitProvenance",
    "ModelReference",
    "SourceFormat",
    "SourceLocation",
    "UnsupportedConstruct",
    "node_list",
]
