"""Headless simulator invocation with LTspice → ngspice fallback.

Two simulators are supported:

- **LTspice** (Wine): runs ``LTspice -b file.asc`` and emits
  ``file.raw``. Used when ``LTSPICE_PATH`` env var (or auto-discovery
  in standard Wine locations) finds the LTspice executable.
- **ngspice**: when LTspice is not available, the schematic is
  netlisted via ``spicelib`` and ngspice runs in batch mode.

Both produce a ``.raw`` file that ``extract.extract_sparams_from_raw``
can consume.

**Success policy.** A run succeeds if and only if it produced the
``.raw`` file. The process return code is *recorded and warned about*
but is not itself a failure condition: LTspice under Wine routinely
exits 1 on a perfectly good run, and ngspice does the same in some
packagings. This matches ``mcp_qucs_s.runner``, which has always keyed
on artifact presence. A stale ``.raw`` is deleted before every run, so
"the file exists" cannot be satisfied by a previous invocation.

**Simulator selection.** Explicit ``prefer=`` wins; otherwise the
``MCP_LTSPICE_SIMULATOR`` env var (``ltspice`` | ``ngspice``) pins the
choice for deployments that want to skip Wine entirely; otherwise
LTspice is preferred for ``.asc`` fidelity, falling back to ngspice.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from rf_mcp_common.logging import get_logger

log = get_logger("mcp_ltspice.runner")

#: Pins simulator selection when ``prefer=`` is not passed. Accepts the
#: same values as :class:`Simulator`.
SIMULATOR_ENV_VAR = "MCP_LTSPICE_SIMULATOR"


class Simulator(StrEnum):
    LTSPICE = "ltspice"
    NGSPICE = "ngspice"


@dataclass
class RunResult:
    raw_path: Path
    log_path: Path
    simulator: Simulator
    returncode: int
    stdout: str
    stderr: str


def find_ltspice() -> Path | None:
    """Locate the LTspice executable.

    Order: ``LTSPICE_PATH`` env var, then ``$PATH``, then the standard
    install locations for Windows, macOS, and Wine (honouring
    ``WINEPREFIX`` if set).
    """
    env = os.environ.get("LTSPICE_PATH")
    if env:
        if Path(env).is_file():
            return Path(env)
        # Don't fall through silently: an operator who set this var and
        # typo'd it would otherwise get ngspice results with no hint that
        # their LTspice setting was ignored.
        log.warning(
            "LTSPICE_PATH=%r does not point to a file; ignoring it and continuing discovery.",
            env,
        )

    for cand in ("LTspice", "ltspice", "XVIIx64.exe"):
        p = shutil.which(cand)
        if p:
            return Path(p)

    home = Path.home()
    wine_root = Path(os.environ.get("WINEPREFIX") or home / ".wine") / "drive_c"
    candidates = [
        # Native Windows
        Path("C:/Program Files/ADI/LTspice/LTspice.exe"),
        Path("C:/Program Files/LTC/LTspiceXVII/XVIIx64.exe"),
        Path("C:/Program Files (x86)/ADI/LTspice/LTspice.exe"),
        # macOS
        Path("/Applications/LTspice.app/Contents/MacOS/LTspice"),
        # Wine
        wine_root / "Program Files/ADI/LTspice/LTspice.exe",
        wine_root / "Program Files/LTC/LTspiceXVII/XVIIx64.exe",
        wine_root / "Program Files (x86)/ADI/LTspice/LTspice.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def find_ngspice() -> Path | None:
    p = shutil.which("ngspice")
    return Path(p) if p else None


def find_wine() -> Path | None:
    p = shutil.which("wine") or shutil.which("wine64")
    return Path(p) if p else None


def simulator_from_env() -> Simulator | None:
    """Read the ``MCP_LTSPICE_SIMULATOR`` pin, or ``None`` if unset.

    Deliberately *not* folded into :func:`find_ltspice`: discovery
    functions report what is installed on the machine, and a caller that
    asks ``find_ltspice()`` while this var says "ngspice" still deserves
    a truthful answer. Selection is a separate concern, handled here and
    in :func:`run_simulation`.
    """
    raw = os.environ.get(SIMULATOR_ENV_VAR, "").strip().lower()
    if not raw:
        return None
    try:
        return Simulator(raw)
    except ValueError:
        valid = ", ".join(s.value for s in Simulator)
        raise RuntimeError(
            f"{SIMULATOR_ENV_VAR}={raw!r} is not a known simulator. Expected one of: {valid}."
        ) from None


def detect_simulator() -> Simulator | None:
    """Return the simulator that would actually be used, or ``None``.

    Honours the ``MCP_LTSPICE_SIMULATOR`` pin when set (returning
    ``None`` if the pinned simulator isn't installed, rather than
    silently using the other one). Otherwise prefers LTspice for real
    ``.asc`` fidelity and falls back to ngspice.
    """
    finders = {Simulator.LTSPICE: find_ltspice, Simulator.NGSPICE: find_ngspice}
    pinned = simulator_from_env()
    if pinned is not None:
        return pinned if finders[pinned]() is not None else None
    if find_ltspice() is not None:
        return Simulator.LTSPICE
    if find_ngspice() is not None:
        return Simulator.NGSPICE
    return None


def _refdes_index(name: str) -> int:
    """Numeric index from a refdes like ``L3`` / ``C12``.

    Raises rather than skipping: a component we cannot order would be
    netlisted in the wrong position, silently simulating a different
    circuit than the one the caller designed.
    """
    m = re.search(r"\d+", name)
    if m is None:
        raise ValueError(f"Component name has no numeric index: {name!r}")
    return int(m.group())


def _needs_wine(exe: Path, os_name: str | None = None) -> bool:
    """True when ``exe`` is a Windows binary we must launch through Wine.

    On native Windows a ``.exe`` runs directly — requiring Wine there
    would make LTspice unusable on the platform it ships for.

    ``os_name`` defaults to the real platform and exists so tests can
    exercise both branches: monkeypatching ``os.name`` globally makes
    ``pathlib`` try to build a ``WindowsPath`` on POSIX, which raises.
    """
    return exe.suffix.lower() == ".exe" and (os_name or os.name) != "nt"


#: Written by LTspice once the first-run consent dialog has been answered.
LTSPICE_SETTINGS_BASENAME = "LTspice.ini"


def _ltspice_settings_files(exe: Path) -> list[Path]:
    """Candidate ``LTspice.ini`` paths inside the active Wine prefix.

    Empty when not running under Wine, where there is no such gate.
    """
    if not _needs_wine(exe):
        return []
    prefix = Path(os.environ.get("WINEPREFIX") or Path.home() / ".wine")
    return list((prefix / "drive_c/users").glob(f"*/AppData/Roaming/{LTSPICE_SETTINGS_BASENAME}"))


def ltspice_first_run_pending(exe: Path) -> bool:
    """True when LTspice under Wine has never had its first-run dialog answered.

    Recent LTspice releases open a modal *"Anonymously Share LTspice Usage
    Data"* dialog the first time they run in a given Wine prefix. It appears
    even under ``-b``, and because batch mode has no one to click it, the
    process blocks until the caller's timeout expires — with an empty log and
    no ``.raw``, which looks nothing like a consent prompt. Answering it once
    writes ``LTspice.ini``, so the file's absence is a reliable preflight
    signal.
    """
    if not _needs_wine(exe):
        return False
    return not any(p.is_file() for p in _ltspice_settings_files(exe))


def _first_run_hint(exe: Path) -> str:
    prefix = Path(os.environ.get("WINEPREFIX") or Path.home() / ".wine")
    ini = prefix / "drive_c/users/<you>/AppData/Roaming" / LTSPICE_SETTINGS_BASENAME
    return (
        "\n\nLikely cause: LTspice has not been run before in this Wine prefix "
        f"({prefix}). Recent releases open a modal 'Anonymously Share LTspice "
        "Usage Data' dialog on first launch, which blocks -b batch mode "
        "indefinitely because nothing can dismiss it.\n"
        "Fix it once, either way:\n"
        f"  1. Launch interactively and answer the prompt:  wine {exe}\n"
        f"  2. Or pre-seed the setting (also opts out of telemetry):\n"
        f"     printf '[Options]\\nCaptureAnalytics=false\\n' > {ini}"
    )


def _to_wine_path(wine: Path, path: Path) -> str:
    """Translate a POSIX path to its Windows form via ``winepath -w``.

    Falls back to the POSIX string if the translation fails — that is no
    worse than not trying, and the artifact check will surface the real
    problem with the full command in the message.
    """
    try:
        return subprocess.check_output(
            [str(wine), "winepath", "-w", str(path)],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=30.0,
        ).strip()
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("winepath translation failed for %s (%s); using the POSIX path", path, e)
        return str(path)


def _check_produced_artifact(
    raw_path: Path,
    proc: subprocess.CompletedProcess[str],
    *,
    simulator: str,
    log_path: Path,
    cmd: list[str],
) -> None:
    """Enforce the success policy: the artifact, not the return code.

    A nonzero return code is warned about but tolerated — LTspice under
    Wine exits 1 on successful runs. A missing ``.raw`` is fatal; the
    caller deleted any stale one first, so its absence means this run
    genuinely produced nothing.
    """
    if proc.returncode != 0:
        log.warning(
            "%s exited with returncode=%d but is being treated as %s; full log at %s",
            simulator,
            proc.returncode,
            "successful (.raw was produced)" if raw_path.is_file() else "failed",
            log_path,
        )
    if not raw_path.is_file():
        raise RuntimeError(
            f"{simulator} did not produce {raw_path}. "
            f"returncode={proc.returncode}. command={' '.join(cmd)!r}. "
            f"stdout={proc.stdout[-500:]!r} stderr={proc.stderr[-500:]!r}. "
            f"Full log at {log_path}"
        )


def _run_ltspice(asc_path: Path, ltspice_exe: Path, *, timeout: float) -> RunResult:
    """Invoke LTspice in batch mode (-b)."""
    raw_path = asc_path.with_suffix(".raw")
    log_path = asc_path.with_suffix(".log")
    if raw_path.exists():
        raw_path.unlink()

    cmd: list[str]
    if _needs_wine(ltspice_exe):
        wine = find_wine()
        if wine is None:
            raise RuntimeError("LTspice.exe found but Wine is not installed")
        # Hand LTspice a Windows-form path. Wine's argv passthrough does
        # not translate POSIX paths, so `-Run /home/u/x.asc` is parsed as
        # a malformed drive spec and the run silently produces no .raw.
        cmd = [str(wine), str(ltspice_exe), "-b", "-Run", _to_wine_path(wine, asc_path)]
    else:
        cmd = [str(ltspice_exe), "-b", "-Run", str(asc_path)]

    if ltspice_first_run_pending(ltspice_exe):
        log.warning(
            "No %s in this Wine prefix, so LTspice has likely never been run here. "
            "If this call hangs until the timeout, it is waiting on the first-run "
            "consent dialog, not simulating.",
            LTSPICE_SETTINGS_BASENAME,
        )

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as e:
        hint = _first_run_hint(ltspice_exe) if ltspice_first_run_pending(ltspice_exe) else ""
        raise RuntimeError(
            f"LTspice produced no result within {timeout:.0f}s. command={' '.join(cmd)!r}{hint}"
        ) from e
    log_path.write_text(
        f"# LTspice command: {' '.join(cmd)}\n"
        f"# returncode: {proc.returncode}\n\n"
        f"=== stdout ===\n{proc.stdout}\n\n"
        f"=== stderr ===\n{proc.stderr}\n",
        encoding="utf-8",
    )
    _check_produced_artifact(raw_path, proc, simulator="LTspice", log_path=log_path, cmd=cmd)
    return RunResult(
        raw_path=raw_path,
        log_path=log_path,
        simulator=Simulator.LTSPICE,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def _asc_to_ngspice_netlist(asc_path: Path) -> Path:
    """Convert one of our generated .asc schematics to an ngspice netlist.

    spicelib's AscEditor needs LTspice's .asy symbol library on disk to
    resolve component definitions, which isn't available in CI. We bypass
    that by parsing the .asc ourselves — our generator emits a known
    subset (V1 source, Rs1/RL1 source/load, L*/C* reactives flagged at
    nodes p1 and p2) so we can build the netlist directly.
    """
    from mcp_ltspice.asc_io import from_ltspice_value, read_components

    components = read_components(asc_path)
    text = asc_path.read_text(encoding="utf-8", errors="replace")
    z0 = 50.0  # default; could be parsed if non-default ever needed

    # Recover the AC sweep directive from the .asc TEXT line
    f_start, f_stop, npts = 1e6, 5e9, 200
    for line in text.splitlines():
        if ".ac dec" in line:
            tokens = line.split(".ac dec", 1)[1].split()
            if len(tokens) >= 3:
                npts = int(tokens[0])
                f_start = from_ltspice_value(tokens[1])
                f_stop = from_ltspice_value(tokens[2])
            break

    indices = sorted({_refdes_index(k) for k in components})

    netlist_lines = ["* Auto-generated ngspice netlist (mcp-ltspice runner)"]
    netlist_lines.append("V1 vsrc 0 AC 1")
    netlist_lines.append(f"Rs1 vsrc p1 {z0:g}")

    # Walk components in numeric order; build a chain of nodes p1 -> n2 -> n3 -> ... -> p2
    node_in = "p1"
    next_node_id = 100  # internal nodes
    for idx in indices:
        l_key = f"L{idx}"
        c_key = f"C{idx}"
        is_trap = l_key in components and c_key in components

        if is_trap:
            # Shunt LC trap to ground from current node
            mid = f"n{next_node_id}"
            next_node_id += 1
            netlist_lines.append(f"L{idx} {node_in} {mid} {components[l_key]:.6g}")
            netlist_lines.append(f"C{idx} {mid} 0 {components[c_key]:.6g}")
        elif l_key in components:
            # Series inductor between node_in and a new node
            new_node = f"n{next_node_id}"
            next_node_id += 1
            netlist_lines.append(f"L{idx} {node_in} {new_node} {components[l_key]:.6g}")
            node_in = new_node
        elif c_key in components:
            # Shunt capacitor to ground from current node
            netlist_lines.append(f"C{idx} {node_in} 0 {components[c_key]:.6g}")

    # Last node -> p2 -> RL1 to ground. If no series elements changed node_in,
    # tie p1 directly to p2 through a 0-ohm resistor for a sane termination.
    if node_in == "p1":
        netlist_lines.append("Rwire p1 p2 1m")
    else:
        netlist_lines.append(f"Rwire {node_in} p2 1m")
    netlist_lines.append(f"RL1 p2 0 {z0:g}")

    netlist_lines.append(f".ac dec {npts} {f_start:g} {f_stop:g}")
    netlist_lines.append("* Save voltages and currents needed for S-param extraction")
    netlist_lines.append(".save V(p1) V(p2) I(Rs1) I(RL1)")
    netlist_lines.append(".end")

    netlist_path = asc_path.with_suffix(".cir")
    netlist_path.write_text("\n".join(netlist_lines) + "\n", encoding="utf-8")
    return netlist_path


def _run_ngspice(asc_path: Path, ngspice_exe: Path, *, timeout: float) -> RunResult:
    netlist = _asc_to_ngspice_netlist(asc_path)
    raw_path = asc_path.with_suffix(".raw")
    log_path = asc_path.with_suffix(".log")
    if raw_path.exists():
        raw_path.unlink()  # parity with LTspice: don't let a stale .raw mask a failure
    cmd = [str(ngspice_exe), "-b", "-r", str(raw_path), "-o", str(log_path), str(netlist)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    # ngspice writes its own log via -o; append our captured stdout/stderr for completeness.
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(
            f"\n# ngspice command: {' '.join(cmd)}\n"
            f"# returncode: {proc.returncode}\n\n"
            f"=== captured stdout ===\n{proc.stdout}\n\n"
            f"=== captured stderr ===\n{proc.stderr}\n"
        )
    _check_produced_artifact(raw_path, proc, simulator="ngspice", log_path=log_path, cmd=cmd)
    return RunResult(
        raw_path=raw_path,
        log_path=log_path,
        simulator=Simulator.NGSPICE,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def run_simulation(
    asc_path: str | Path,
    *,
    prefer: Simulator | None = None,
    timeout: float = 120.0,
) -> RunResult:
    """Run the simulation. Returns a :class:`RunResult` with the raw path.

    ``prefer`` forces a specific simulator; if that's not installed the
    function raises ``RuntimeError`` rather than silently falling back.
    When ``prefer`` is omitted, ``MCP_LTSPICE_SIMULATOR`` pins the choice
    the same way (and is likewise strict about the binary being present).
    """
    asc_path = Path(asc_path).expanduser().resolve()
    if not asc_path.is_file():
        raise FileNotFoundError(f".asc not found: {asc_path}")

    if prefer is None:
        prefer = simulator_from_env()

    if prefer == Simulator.LTSPICE:
        exe = find_ltspice()
        if exe is None:
            raise RuntimeError("LTspice not found (set $LTSPICE_PATH)")
        return _run_ltspice(asc_path, exe, timeout=timeout)
    if prefer == Simulator.NGSPICE:
        exe = find_ngspice()
        if exe is None:
            raise RuntimeError("ngspice not found on PATH (`apt install ngspice`)")
        return _run_ngspice(asc_path, exe, timeout=timeout)

    # Auto: prefer LTspice for .asc fidelity, fall back to ngspice
    exe = find_ltspice()
    if exe is not None:
        return _run_ltspice(asc_path, exe, timeout=timeout)
    exe = find_ngspice()
    if exe is not None:
        return _run_ngspice(asc_path, exe, timeout=timeout)
    raise RuntimeError(
        "No SPICE simulator found. Install ngspice (`apt install ngspice`) or LTspice via Wine."
    )
