"""Shared contracts for the RF MCP suite."""

from rf_mcp_common.version import distribution_version

__version__ = distribution_version("rf-mcp-common")

from rf_mcp_common.backend import (
    DEFAULT_TOLERANCE_POLICIES,
    BackendAdapter,
    BackendArtifact,
    BackendCapability,
    ResultDataset,
    TolerancePolicy,
    compare_datasets,
)
from rf_mcp_common.circuit_ir import (
    CIRCUIT_SCHEMA_VERSION,
    CircuitAnalysis,
    CircuitChange,
    CircuitComponent,
    CircuitDocument,
    CircuitNode,
    CircuitPort,
    UnsupportedConstruct,
)
from rf_mcp_common.ecomp import ESeries, snap_to_eseries
from rf_mcp_common.envelope import Envelope, error, ok
from rf_mcp_common.logging import JsonFormatter, get_logger, tool_timer
from rf_mcp_common.optimization import (
    DesignCorner,
    EvaluationResult,
    MetricConstraint,
    MetricObjective,
    OptimizationProblem,
    OptimizationResult,
    OptimizationVariable,
    optimize_circuit,
    render_design_change_report,
)
from rf_mcp_common.simulation_workspace import (
    SimulationWorkspace,
    probe_executable_version,
    sha256_file,
    subprocess_environment,
)
from rf_mcp_common.tool_annotations import (
    DEFAULT_TOOL_ANNOTATIONS,
    READ_ONLY_TOOL_ANNOTATIONS,
)
from rf_mcp_common.tool_errors import EnvelopeErrorMiddleware
from rf_mcp_common.touchstone import (
    network_to_touchstone,
    read_touchstone,
    sparams_at,
    write_touchstone,
)
from rf_mcp_common.units import FreqUnit, db, dbm_to_w, hz, lin, w_to_dbm

__all__ = [
    "CIRCUIT_SCHEMA_VERSION",
    "DEFAULT_TOLERANCE_POLICIES",
    "DEFAULT_TOOL_ANNOTATIONS",
    "READ_ONLY_TOOL_ANNOTATIONS",
    "BackendAdapter",
    "BackendArtifact",
    "BackendCapability",
    "CircuitAnalysis",
    "CircuitChange",
    "CircuitComponent",
    "CircuitDocument",
    "CircuitNode",
    "CircuitPort",
    "DesignCorner",
    "ESeries",
    "Envelope",
    "EnvelopeErrorMiddleware",
    "EvaluationResult",
    "FreqUnit",
    "JsonFormatter",
    "MetricConstraint",
    "MetricObjective",
    "OptimizationProblem",
    "OptimizationResult",
    "OptimizationVariable",
    "ResultDataset",
    "SimulationWorkspace",
    "TolerancePolicy",
    "UnsupportedConstruct",
    "__version__",
    "compare_datasets",
    "db",
    "dbm_to_w",
    "error",
    "get_logger",
    "hz",
    "lin",
    "network_to_touchstone",
    "ok",
    "optimize_circuit",
    "probe_executable_version",
    "read_touchstone",
    "render_design_change_report",
    "sha256_file",
    "snap_to_eseries",
    "sparams_at",
    "subprocess_environment",
    "tool_timer",
    "w_to_dbm",
    "write_touchstone",
]
