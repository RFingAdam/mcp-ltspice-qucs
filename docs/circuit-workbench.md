# General circuit workbench

The suite has a shared, versioned circuit intermediate representation (IR) for
importing files, preserving connectivity, compiling simulator inputs, selecting
models, and optimizing values. The current schema is `CircuitDocument` 1.0.

This is a bounded parser architecture: a source construct is either represented
with explicit electrical semantics or returned in `unsupported` with a stable
code and source location. A partially imported document may be inspected, but
export and simulation call `require_supported()` and refuse blocking
diagnostics.

## CircuitDocument 1.0

The electrical graph contains:

- uniquely named nodes, including an explicit ground node;
- components with a reference designator, kind, pin-to-net map, value,
  parameters, optional drawing geometry, and optional immutable model
  reference;
- ports and impedance fixtures;
- normalized analyses plus preserved source directives;
- include, library, model, subcircuit, and Touchstone dependencies;
- source hashes, transformation history, and unsupported-construct
  diagnostics.

Every transformation creates a copy and a change set. The helper accepts only
component value and parameter paths such as
`components.L1.value` or `components.Q1.parameters.area`; connectivity cannot
be mutated accidentally through the optimization API.

`electrical_fingerprint()` hashes nodes, components, ports, analyses,
directives, and dependencies. `connectivity_signature()` supports explicit
round-trip verification.

## Import and export support

| Format | Supported electrical subset | Explicitly rejected or preserved |
|---|---|---|
| SPICE `.cir`, `.sp`, `.net`, `.spice` | R, L, C, independent and dependent sources, D, BJT (3/4 terminal), JFET, MOSFET, switches, transmission lines, and subcircuit instances; common analyses, models, parameters, and include/library dependencies | Interactive `.control` commands block compilation. Subcircuit definitions are preserved but currently reported as unsupported hierarchical definitions. Unknown element prefixes are line-specific errors. |
| LTspice `.asc` | Stock two-terminal resistor, capacitor, inductor, voltage, and current symbols; rotations/mirroring; orthogonal wires; flags; attributes; directives; drawing geometry | Unknown symbol pin geometry, missing instance names, diagonal wires, and unknown electrical records block compilation. Pure drawing primitives are preserved as warnings. |
| Qucsator netlist | R, L, C, ports, sources, diode, ideal/coupled/microstrip lines, circulator, substrate records, and supported analyses | Unknown component models or malformed node lists block compilation. |
| Qucs/Qucs-S `.sch` | R, L, C, two-terminal sources/ports, ground, orthogonal wires, labels, rotations/mirroring, and supported analysis records | Any component without registered schematic pin geometry blocks compilation. Diagonal wires block compilation. |

Unchanged supported files can round-trip byte-for-byte. Value changes preserve
the original LTspice or Qucs drawing records; normalized SPICE/Qucs netlist
exports are deterministic. See the official [Qucs component/file-format
description](https://qucs.github.io/qucs-manual/0.0.19/html-ar/component_description.html)
for the source record conventions used by the Qucs importer.

The canonical MCP flow is:

1. `workspace_create`
2. `artifact_import`
3. `circuit_parse`
4. inspect `is_supported` and `unsupported`, or call `circuit_validate`
5. optionally `circuit_attach_models`
6. `circuit_export`, `simulation_submit`, or `circuit_optimize_submit`

The LTspice server handles LTspice/SPICE IR and exposes the full job set:
`circuit_validate`, `simulation_submit`, `circuit_optimize_submit`, `job_get`,
`job_cancel`, `job_retry`, `job_list_artifacts`, `artifact_read`. The Qucs
server exposes the same workspace/parse/validate/export shape for Qucs
schematics and netlists, plus a `simulation_submit` job that drives
`QucsatorAdapter`/`XyceAdapter` through compile/run/parse/validate and the same
job tools; `analysis.kind` selects the backend (`sparameters`/`noise` →
Qucsator, `harmonic_balance` → Xyce). Generated files are returned as artifact
IDs and `artifact://` resources, not as paths that a remote client must be
able to read.

## Backend adapter contract

Every adapter implements the same operations:

```text
probe() -> BackendCapability
import_file() -> CircuitDocument
compile(document, analysis) -> BackendArtifact
run(request) -> RawBackendResult
parse(raw) -> ResultDataset
validate(dataset, analysis) -> ValidationReport
```

The implementations are:

- `NgspiceAdapter`
- `LTspiceAdapter`
- `QucsatorAdapter`
- `XyceAdapter`

Capability negotiation rejects unsupported analyses before submission.
Compiled artifacts contain the source IR fingerprint, input SHA-256, target
backend, analysis, and exact selected-model hashes. SPICE model includes are
checksum-verified, copied into the evaluation workspace, and rewritten to
workspace-relative paths before launch.

Qucsator generic compilation currently rejects attached cross-dialect component
models. Use a native Qucs model or validate a selected SPICE/subcircuit model
with ngspice, LTspice, or Xyce. This rejection is intentional; Qucsator does not
silently receive an ideal replacement.

Qucsator and Xyce have no verified OS sandbox profile
(`BackendCapability.sandbox_profile.available` is always `false`), so
`QucsatorAdapter.run`/`XyceAdapter.run` refuse `sandbox=true` outright and the
Qucs server's `simulation_submit` job always runs them unsandboxed on the
immutable per-run workspace snapshot the adapter creates. Only submit trusted
local inputs through it.

## Normalized results and tolerances

`ResultDataset` stores one strictly increasing axis and JSON-safe real/imaginary
trace arrays. Frequency axes normalize to hertz and time axes to seconds.
Cross-backend comparison uses the union of both grids inside their overlap,
interpolates real and imaginary values separately, and never extrapolates.
Phase differences use circular phase.

| Analysis | Default comparison thresholds |
|---|---|
| Operating point | relative `1e-4`, absolute `1e-9` |
| DC sweep | relative `1e-3`, absolute `1e-8` |
| AC | magnitude `0.10 dB`, phase `1°`, complex absolute `1e-3` |
| S-parameters | magnitude `0.10 dB`, phase `1°`, complex absolute `1e-2` |
| Transient | relative `2e-3`, absolute `1e-8` |
| Noise | magnitude `0.20 dB`, relative `1e-2` |
| Harmonic balance | magnitude `0.50 dB`, phase `3°`, relative `5e-2` |

Policies have stable IDs and rationale strings. A known-answer asymmetric
two-port fixture runs on both ngspice and Qucsator and compares all four
S-parameters under this policy.

## Component search and model realization

`search_component_models` searches curated and registered local providers with
hard constraints for:

- kind and nominal value/range;
- exact package;
- in-stock, orderable, or explicitly generic status;
- Q at a stated frequency and minimum SRF;
- maximum tolerance;
- voltage/current or other named ratings;
- operating bias and temperature;
- model kind and provider.

Unknown catalog data fails a requested constraint. It is never assumed to meet
the constraint. Every hit says `selection_class: orderable` or
`selection_class: generic` and returns the immutable model record and SHA-256.

`circuit_attach_models` validates the pin count and adds the model identity,
validity ranges, provenance, license, package, availability class, source
dependency, and checksum to the IR.

For SPICE-family compilation:

- a two-terminal `.subckt` is included and instantiated verbatim;
- primitive `.model` records are included and referenced;
- curated lumped approximations expand into explicit ESR plus L/C parasitic
  subcircuits;
- a Touchstone record requires a declared lumped reduction for SPICE export,
  otherwise compilation fails.

## Generic optimization

`circuit_optimize_submit` is a durable job over IR parameter paths. It accepts:

- bounded linear/log variables or discrete choices;
- minimize, maximize, and target objectives;
- less-than, greater-than, and range constraints;
- named environmental and component-multiplier corners;
- deterministic seed and iteration limit;
- per-variable tolerances and a bounded yield sample count;
- normalized simulator trace metrics (`at`, `min`, `max`, `mean`, or `rms`);
- screening and independent validation backends.

SPICE evaluator environment corners are explicit: `temperature_c` emits
`.temp`, and `param.<name>` emits a finite numeric/bool `.param`. Unknown
environment keys fail rather than being ignored. Component multipliers and
value variables can tune generic lumped approximations. The nominal value of a
fixed subcircuit, primitive model, or Touchstone model cannot be varied; use an
explicit instance parameter or select a different model.

Every requested metric must be returned at every corner. Required frequencies
are evaluated by interpolation only inside the simulator result range;
extrapolation is an error. Phase interpolation unwraps across ±180°. The
optimizer never changes component connectivity, and feasible candidates always
rank ahead of infeasible candidates before objective score is considered.

When model-aware validation is requested, each final corner and every yield
sample must attest exactly the selected `{refdes: model_sha256}` map. A missing,
extra, or changed hash blocks a `simulator_validated` result. If independent
validation is requested, the screening and validation backends must differ.

The result artifact contains the complete deterministic evaluation trace. The
separate Markdown design-change report includes:

- original and final IR fingerprints;
- every value/parameter change;
- objectives and constraint result by corner;
- screening and validation backend;
- independent-validation status;
- selected model hashes;
- yield seed, sample count, pass count, and fraction.

Long-running optimization uses the durable job state machine and supports
polling, progress, cancellation, retry, and artifact resources.

## Confidence labels

- `screened`: objectives were evaluated, but independent simulator/model
  validation requirements were not all met.
- `simulator_validated`: a simulator produced the final corner results, selected
  model hashes matched exactly, constraints passed, and any requested
  independent-backend condition passed.
- `constraints_failed`: the final validation did not meet all required
  constraints.

These labels do not infer simulator evidence from an analytical or approximate
result.
