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


def find_qucs_s() -> Path | None:
    """Locate the qucs-s / qucsator executable.

    Checks the ``QUCS_S_PATH`` env var, then ``$PATH``, then standard
    install locations.
    """
    env = os.environ.get("QUCS_S_PATH")
    if env and Path(env).is_file():
        return Path(env)

    for cand in ("qucs-s", "qucsator", "qucsator_rf"):
        p = shutil.which(cand)
        if p:
            return Path(p)

    home = Path.home()
    candidates = [
        home / ".local/bin/qucs-s",
        home / ".local/bin/qucsator",
        Path("/usr/local/bin/qucs-s"),
        Path("/Applications/Qucs-S.app/Contents/MacOS/qucs-s"),
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def find_xyce() -> Path | None:
    p = shutil.which("xyce") or shutil.which("Xyce")
    return Path(p) if p else None


def is_qucs_available() -> bool:
    return find_qucs_s() is not None


def is_xyce_available() -> bool:
    return find_xyce() is not None


def run_qucs(
    sch_path: str | Path,
    *,
    output_path: str | Path | None = None,
    timeout_sec: float = 300.0,
) -> QucsRunResult:
    """Invoke qucsator headlessly: ``qucsator -i in.sch -o out.dat``."""
    sch = Path(sch_path).expanduser().resolve()
    if not sch.is_file():
        raise FileNotFoundError(f"Schematic not found: {sch}")

    exe = find_qucs_s()
    if exe is None:
        raise RuntimeError(
            "Qucs-S / qucsator not found. Install Qucs-S from source "
            "(see docs/installation.md) or set $QUCS_S_PATH."
        )

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
