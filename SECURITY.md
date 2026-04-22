# Security Policy

## Reporting a vulnerability

If you discover a security issue in this project, please report it
privately rather than opening a public GitHub issue:

- Email: adam.engelbrecht76@gmail.com
- GitHub: [security advisories](https://github.com/RFingAdam/mcp-ltspice-qucs/security/advisories)
  (use "Report a vulnerability")

Please include:

- A description of the issue and the package(s) affected
- Steps to reproduce, ideally with a minimal example
- The version(s) you tested against
- Any suggested mitigations or fixes

We aim to acknowledge reports within 72 hours and to release a fix or
mitigation within 30 days for high-severity issues.

## Scope

This is a developer-facing MCP toolkit, not a network-facing service.
Realistic threat models include:

- **Untrusted Touchstone / `.asc` input**: a malicious file could attempt
  to exploit the SPICE simulator (via crafted netlist) or `spicelib`.
  We rely on the upstream simulator's input handling; if you parse files
  from untrusted sources, run the parser/runner in a sandbox.
- **Untrusted MCP tool arguments**: the servers validate inputs via
  Pydantic models, but path arguments are not sandboxed by default. If
  you expose these MCP servers to an untrusted LLM agent or user, run
  them inside a container or chroot with restricted filesystem access.
- **Vendor SPICE models** (`vendor_models.py`): the bundled tables are
  static numbers. If you extend them with downloaded SPICE subcircuits
  from a vendor site, treat those subcircuits as untrusted code that
  the simulator will execute.

## What's *not* in scope

- DoS by passing extremely large `n_runs` to `monte_carlo_analysis` —
  this is by design; rate-limit at the MCP transport layer if needed.
- Bugs in dependencies (`scipy`, `skrf`, `spicelib`, `fastmcp`,
  `numpy`). Report those upstream.

## Supported versions

We support the latest release of each package on the `main` branch.
There is no LTS for older versions during the 0.x series.
