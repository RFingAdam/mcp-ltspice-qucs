# Contributing

Thanks for your interest in `mcp-ltspice-qucs`. This document covers
the basics of getting a development environment running and submitting
changes.

## Quick setup

```bash
git clone https://github.com/RFingAdam/mcp-ltspice-qucs
cd mcp-ltspice-qucs
uv sync --all-packages
uv run pytest -q
```

You should see 427 tests pass and 4 skip. The skips are simulator-gated
(LTspice/ngspice/Qucs-S/Xyce); install any of them (see the
[Installation guide](https://github.com/RFingAdam/mcp-ltspice-qucs/blob/main/docs/installation.md))
to exercise the simulator-driven paths.

## Layout

The repo is a uv workspace with four packages under `packages/`:

- `rf-mcp-common` — shared envelope, Touchstone I/O, E-series snap, logging
- `mcp-ltspice` — LTspice + ngspice runner, filter synthesis, spec eval
- `mcp-rf-analysis` — skrf wrappers, regulatory band DBs, coex matrix
- `mcp-qucs-s` — Qucs-S native S-param + harmonic balance + microstrip

See [ARCHITECTURE](https://github.com/RFingAdam/mcp-ltspice-qucs/blob/main/ARCHITECTURE.md) for the inter-server contract.

## Running tests

```bash
uv run pytest                          # all packages
uv run pytest packages/mcp-ltspice/    # one package
uv run pytest -k "synthesis"           # by keyword
uv run pytest -m ngspice               # only simulator-integration
uv run pytest --cov                    # with coverage
```

## Linting & typing

Both run via the uv-managed dev tools:

```bash
uv run ruff format          # auto-format
uv run ruff check --fix     # autofix what's safe
uv run mypy packages/       # strict types
```

Pre-commit hooks (configured in `.pre-commit-config.yaml`, enable with
`uv run pre-commit install`) run ruff format + ruff check + mypy on
every commit.

## Adding a new tool to a server

1. Implement the underlying logic in a new module under
   `packages/<server>/src/<pkg>/`. Pure functions — don't touch
   FastMCP yet.
2. Write tests under `packages/<server>/tests/` that exercise it
   without any simulator dependency where possible.
3. Wire it into `server.py` with `@mcp.tool(description=...)`. Wrap the
   call in `Timer()` + `ok(...)` / `error(...)` so the response envelope
   stays consistent.
4. Run `uv run pytest packages/<server>` and confirm green.
5. Add an entry to the per-server `README.md` tool catalogue.
6. Update `CHANGELOG.md` under `[Unreleased]`.

## Adding a new MCP server

Three steps:

1. Scaffold `packages/<new>/` with the same layout as one of the
   existing servers. Add it to `tool.uv.workspace.members` in the
   root `pyproject.toml`.
2. Depend on `rf-mcp-common` so the envelope and Touchstone helpers
   are available — don't reimplement either.
3. Use Touchstone files for any inter-server data exchange. The
   contract is documented in [ARCHITECTURE](https://github.com/RFingAdam/mcp-ltspice-qucs/blob/main/ARCHITECTURE.md).

## Testing conventions

- One module of source ↔ one test file, named `test_<module>.py`.
- Tests should not write to the repo working tree; use the `tmp_path`
  pytest fixture for any file output.
- Fixtures that build sample networks live in the package's
  `conftest.py`, not at the repo root, so packages stay decoupled.
- Mark simulator-requiring tests with `@pytest.mark.ngspice` or
  `@pytest.mark.ltspice` so they skip cleanly when the simulator is
  absent.

## Commit messages

Conventional Commits style: `<type>(<scope>): <subject>`. Common types:
`feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`, `perf`.

Examples:

- `feat(rf-analysis): add 5G NR FR2 band lookup`
- `fix(ltspice): handle resonance singularity in shunt LC trap`
- `docs: clarify Touchstone interop contract`

## Releases

We follow [Keep a Changelog](https://keepachangelog.com/) +
[Semver](https://semver.org/). Each package versions independently:

- Bump `version` in `packages/<name>/pyproject.toml`
- Move entries from `[Unreleased]` to a new dated section in `CHANGELOG.md`
- Tag: `git tag <name>-v<version> && git push --tags`

CI runs the test matrix on every push and PR. PyPI publishing is
not yet wired up; install from a git ref or build wheels locally
with `uv build` until that lands.

## Code of conduct

We follow the [Contributor Covenant](https://www.contributor-covenant.org/).
See [`CODE_OF_CONDUCT.md`](https://github.com/RFingAdam/mcp-ltspice-qucs/blob/main/.github/CODE_OF_CONDUCT.md).
