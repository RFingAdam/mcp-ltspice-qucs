<!--
Thanks for the PR. A short prose description above the checklist is
usually enough — bullet what changed and why, not how.
-->

## Summary

<!-- What changed? Why? Link any related issue: "Closes #123". -->

## Verification

<!-- How did you test? Paste the relevant test output or commands. -->

- [ ] `uv run pytest -q` passes locally
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run ruff check .` passes
- [ ] If touching a server tool: smoke-tested via `uv run mcp-<server>` and
      a manual `call_tool` round-trip
- [ ] If touching CI / packaging / install paths: ran the change in a
      fresh `uv sync --all-packages --reinstall` venv

## Changelog

<!-- Add a one-line entry under [Unreleased] in CHANGELOG.md. Skip if this
     is purely internal / docs. -->

- [ ] CHANGELOG.md updated, or N/A
