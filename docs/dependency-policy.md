# Dependency and release policy

Public wheels use lower and upper bounds. Runtime libraries are capped below
the next incompatible major version; FastMCP and AnyIO are capped to the tested
minor line because they directly determine MCP transport behavior. Workspace
packages remain on the same `0.5.x` compatibility line.

`uv.lock` is the reproducible development and CI environment. Every ordinary
CI sync uses `--frozen`, so a manifest edit without a reviewed lock update
fails instead of resolving opportunistically.

## Compatibility gates

Every change is checked in two dependency modes:

1. The locked matrix on Python 3.11, 3.12, and 3.13, plus Windows.
2. `uv lock --resolution lowest-direct` on Python 3.11 for the minimum direct
   runtime versions declared by the package manifests. That environment
   excludes the development group and adds only the bounded pytest runner, so
   newer documentation/lint transitive dependencies cannot mask a runtime
   compatibility failure.

The protocol suite performs real MCP initialize, tool discovery, canonical
tool calls, and error-envelope checks. The tool-contract snapshot protects
names, schemas, annotations, and alias deprecation metadata.

## Upgrade process

Dependency upgrades are intentional pull requests:

1. Update bounds only for a specific compatibility target.
2. Regenerate `uv.lock`.
3. Run the locked and minimum dependency gates.
4. Regenerate the tool contract snapshot when FastMCP changes its exposed
   schema.
5. Run real simulator integrations affected by numerical or parser changes.
6. Record user-visible or compatibility-relevant changes in the changelog.

Routine patch/minor refreshes should be reviewed monthly. Security fixes can
be expedited, but they pass the same protocol and numerical known-answer
tests. Major-version upgrades require an explicit migration change rather than
an unbounded resolver update.

## Release artifacts

Tag validation builds all four wheels and source distributions, installs
wheels in isolation, audits the exported locked requirements, generates an
SPDX SBOM, and creates GitHub build-provenance attestations. A release is not
ready when generated documentation, versions, client configuration, or MCP
contracts are stale.
