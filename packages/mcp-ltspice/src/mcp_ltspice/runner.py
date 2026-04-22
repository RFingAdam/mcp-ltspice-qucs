"""Headless simulator invocation with LTspice → ngspice fallback.

Two simulators are supported:

- **LTspice** (Wine): runs ``LTspice -b file.asc`` and emits
  ``file.raw``. Used when ``LTSPICE_PATH`` env var (or auto-discovery
  in standard Wine locations) finds the LTspice executable.
- **ngspice**: when LTspice is not available, the schematic is
  netlisted via ``spicelib`` and ngspice runs in batch mode.

Both produce a ``.raw`` file that ``extract.extract_sparams_from_raw``
can consume.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


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
    """Locate the LTspice executable. Checks env var, $PATH, and common
    Wine install locations.
    """
    env = os.environ.get("LTSPICE_PATH")
    if env and Path(env).is_file():
        return Path(env)

    for cand in ("LTspice", "ltspice", "XVIIx64.exe"):
        p = shutil.which(cand)
        if p:
            return Path(p)

    home = Path.home()
    candidates = [
        home / ".wine/drive_c/Program Files/ADI/LTspice/LTspice.exe",
        home / ".wine/drive_c/Program Files/LTC/LTspiceXVII/XVIIx64.exe",
        home / ".wine/drive_c/Program Files (x86)/ADI/LTspice/LTspice.exe",
        Path("/Applications/LTspice.app/Contents/MacOS/LTspice"),
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


def detect_simulator() -> Simulator | None:
    """Return the preferred simulator that's actually installed.

    Preference: LTspice (real .asc fidelity) > ngspice (netlist).
    """
    if find_ltspice() is not None:
        return Simulator.LTSPICE
    if find_ngspice() is not None:
        return Simulator.NGSPICE
    return None


def _run_ltspice(asc_path: Path, ltspice_exe: Path, *, timeout: float) -> RunResult:
    """Invoke LTspice in batch mode (-b)."""
    raw_path = asc_path.with_suffix(".raw")
    log_path = asc_path.with_suffix(".log")
    if raw_path.exists():
        raw_path.unlink()

    cmd: list[str]
    if ltspice_exe.suffix.lower() == ".exe":
        wine = find_wine()
        if wine is None:
            raise RuntimeError("LTspice.exe found but Wine is not installed")
        cmd = [str(wine), str(ltspice_exe), "-b", "-Run", str(asc_path)]
    else:
        cmd = [str(ltspice_exe), "-b", "-Run", str(asc_path)]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if not raw_path.is_file():
        raise RuntimeError(
            f"LTspice did not produce {raw_path}. stdout={proc.stdout[-500:]!r} "
            f"stderr={proc.stderr[-500:]!r}"
        )
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

    indices = sorted({int(re.search(r"\d+", k).group()) for k in components})

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
    cmd = [str(ngspice_exe), "-b", "-r", str(raw_path), "-o", str(log_path), str(netlist)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if not raw_path.is_file():
        raise RuntimeError(
            f"ngspice did not produce {raw_path}. stdout={proc.stdout[-500:]!r} "
            f"stderr={proc.stderr[-500:]!r}"
        )
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
    """
    asc_path = Path(asc_path).expanduser().resolve()
    if not asc_path.is_file():
        raise FileNotFoundError(f".asc not found: {asc_path}")

    if prefer == Simulator.LTSPICE:
        exe = find_ltspice()
        if exe is None:
            raise RuntimeError("LTspice not found (set $LTSPICE_PATH)")
        return _run_ltspice(asc_path, exe, timeout=timeout)
    if prefer == Simulator.NGSPICE:
        exe = find_ngspice()
        if exe is None:
            raise RuntimeError("ngspice not found on PATH")
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
