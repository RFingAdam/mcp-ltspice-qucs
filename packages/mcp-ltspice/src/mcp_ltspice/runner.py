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
    """Convert an .asc to a flat ngspice-compatible netlist via spicelib."""
    from spicelib import AscEditor, SpiceEditor

    editor = AscEditor(str(asc_path))
    netlist_path = asc_path.with_suffix(".cir")
    editor.save_netlist(str(netlist_path))

    # Ensure the .cir uses ngspice-friendly directives. spicelib usually
    # emits LTspice-style; the ngspice runner generally accepts these.
    text = netlist_path.read_text(encoding="utf-8", errors="replace")
    if not text.strip().endswith(".end"):
        text += "\n.end\n"
    netlist_path.write_text(text, encoding="utf-8")
    _ = SpiceEditor  # used for type/import side-effect
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
