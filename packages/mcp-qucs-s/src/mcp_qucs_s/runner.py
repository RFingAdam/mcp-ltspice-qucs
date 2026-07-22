"""Headless Qucs-S invocation.

Detection-first design: tools that need Qucs-S installed return clean
error envelopes when it's missing instead of crashing. This keeps the
synthesis tools (which don't need a simulator) usable on any machine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class QucsRunResult:
    output_path: Path
    log_path: Path
    returncode: int
    stdout: str
    stderr: str


#: Headless simulation engines, best first. ``qucs-s`` is deliberately
#: absent: it is the Qt GUI, and handing it ``-i netlist -o dat`` opens a
#: window and blocks forever on a headless box instead of simulating.
QUCS_ENGINE_BINARIES = ("qucsator_rf", "qucsator")

#: The GUI. Only used to tell "nothing installed" apart from "GUI installed
#: but the engine was never built", which is what a clone missing
#: ``--recurse-submodules`` leaves behind.
QUCS_GUI_BINARIES = ("qucs-s",)


def find_qucs_gui() -> Path | None:
    """Locate the Qucs-S GUI, for diagnostics only — it cannot simulate."""
    for cand in QUCS_GUI_BINARIES:
        p = shutil.which(cand)
        if p:
            return Path(p)
    for c in (Path.home() / ".local/bin/qucs-s", Path("/usr/local/bin/qucs-s")):
        if c.is_file():
            return c
    return None


def find_qucs_s() -> Path | None:
    """Locate the qucsator simulation engine.

    Checks the ``QUCS_S_PATH`` env var, then ``$PATH``, then standard
    install locations. Returns the *engine*, never the GUI.
    """
    env = os.environ.get("QUCS_S_PATH")
    if env and Path(env).is_file():
        return Path(env)

    for cand in QUCS_ENGINE_BINARIES:
        p = shutil.which(cand)
        if p:
            return Path(p)

    home = Path.home()
    candidates = [
        home / ".local/bin/qucsator_rf",
        home / ".local/bin/qucsator",
        Path("/usr/local/bin/qucsator_rf"),
        Path("/usr/local/bin/qucsator"),
        Path("/Applications/Qucs-S.app/Contents/MacOS/qucsator_rf"),
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _missing_engine_message() -> str:
    gui = find_qucs_gui()
    if gui is not None:
        return (
            f"Found the Qucs-S GUI at {gui}, but not the qucsator simulation "
            f"engine ({' or '.join(QUCS_ENGINE_BINARIES)}), which is what runs "
            "headless netlists. The usual cause is cloning qucs_s without "
            "--recurse-submodules, so the qucsator-RF submodule was never "
            "built. See docs/installation.md."
        )
    return (
        "Qucs-S / qucsator not found. Install Qucs-S from source "
        "(see docs/installation.md) or set $QUCS_S_PATH to the qucsator binary."
    )


def find_xyce() -> Path | None:
    p = shutil.which("xyce") or shutil.which("Xyce")
    return Path(p) if p else None


def is_qucs_available() -> bool:
    return find_qucs_s() is not None


def is_xyce_available() -> bool:
    return find_xyce() is not None


def run_qucs(
    netlist_path: str | Path,
    *,
    output_path: str | Path | None = None,
    timeout_sec: float = 300.0,
) -> QucsRunResult:
    """Invoke qucsator headlessly: ``qucsator -i in.net -o out.dat``.

    Takes a qucsator *netlist*, not the GUI's ``.sch`` file — the Qucs GUI
    netlists a schematic before handing it to the engine. Generate one
    with :func:`mcp_qucs_s.netlist.generate_ladder_netlist`.
    """
    sch = Path(netlist_path).expanduser().resolve()
    if not sch.is_file():
        raise FileNotFoundError(f"Netlist not found: {sch}")

    exe = find_qucs_s()
    if exe is None:
        raise RuntimeError(_missing_engine_message())

    out = Path(output_path).expanduser().resolve() if output_path else sch.with_suffix(".dat")
    log = sch.with_suffix(".qucs.log")
    cmd = [str(exe), "-i", str(sch), "-o", str(out)]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )
    log.write_text(
        f"$ {' '.join(cmd)}\n=== STDOUT ===\n{proc.stdout}\n=== STDERR ===\n{proc.stderr}",
        encoding="utf-8",
    )
    if not out.is_file():
        raise RuntimeError(
            f"Qucs-S did not produce {out}. stdout={proc.stdout[-500:]!r} "
            f"stderr={proc.stderr[-500:]!r}"
        )
    return QucsRunResult(
        output_path=out,
        log_path=log,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )
