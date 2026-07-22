"""Harmonic-balance analysis via the Xyce backend.

Harmonic balance solves for the steady-state spectrum of a nonlinear circuit
directly, rather than integrating a transient until it settles. That is what
makes intermodulation practical to compute: a −60 dBc IM3 product is hopeless
to extract from a transient FFT but falls straight out of an HB solve.

What this module drives:

- **One tone** — harmonic content at ``k·f0``, i.e. harmonic distortion.
- **Two tones** — the intermodulation products, from which IM3, OIP3 and
  IIP3 follow. Third-order products at ``2f1−f2`` and ``2f2−f1`` land just
  outside the tone pair and are the ones that fall in-band in a real radio.
- **Power sweeps** — gain compression, giving P1dB.

Xyce specifics, established against Xyce 7.10.0 rather than from the manual:

- ``.HB f1 [f2]`` sets the fundamentals, and ``.OPTIONS HBINT NUMFREQ=n[,n]``
  the harmonic count — with **one entry per tone**. A single ``NUMFREQ`` with
  two tones aborts the run with "The size of numFreq does not match the
  number of tones in .hb!".
- ``.PRINT HB_FD`` writes ``<netlist>.HB.FD.prn``, a **two-sided** spectrum
  (negative and positive frequencies) with columns
  ``Index FREQ Re(V(NODE)) Im(V(NODE))``. Single-sided amplitude at a
  positive frequency is therefore twice the magnitude in that row.
- Xyce writes its output files **next to the netlist**, not into the working
  directory, so every run gets its own directory.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from rf_mcp_common.logging import get_logger

log = get_logger("mcp_qucs_s.harmonic_balance")

#: Products this far apart in frequency (relative) are treated as the same bin.
_BIN_RTOL = 1e-6


def find_xyce() -> Path | None:
    p = shutil.which("Xyce") or shutil.which("xyce")
    return Path(p) if p else None


def dbm_to_source_amplitude(dbm: float, z0: float = 50.0) -> float:
    """Peak source voltage delivering ``dbm`` of *available* power into ``z0``.

    Available power from a source of peak amplitude ``V`` behind ``z0`` is
    ``V²/(8·z0)`` — the factor of 8 covers both the peak-to-RMS conversion and
    the halving across the matched divider. Getting this wrong shifts every
    reported dBm by a constant, which is invisible in a spectrum plot but
    wrecks IIP3.
    """
    p_watts = 10.0 ** ((dbm - 30.0) / 10.0)
    return math.sqrt(8.0 * z0 * p_watts)


def volts_to_dbm(v_peak: float | NDArray[np.float64], z0: float = 50.0):
    """Peak volts across ``z0`` to dBm."""
    p_watts = np.asarray(v_peak) ** 2 / (2.0 * z0)
    with np.errstate(divide="ignore"):
        return 10.0 * np.log10(np.maximum(p_watts, 1e-300) / 1e-3)


@dataclass
class HBSpectrum:
    """Single-sided spectrum at the output node."""

    freqs_hz: NDArray[np.float64]
    volts_peak: NDArray[np.float64]
    dbm: NDArray[np.float64]

    def at(self, target_hz: float) -> tuple[float, float]:
        """``(volts_peak, dbm)`` in the bin nearest ``target_hz``."""
        if self.freqs_hz.size == 0:
            raise ValueError("Spectrum is empty.")
        i = int(np.argmin(np.abs(self.freqs_hz - target_hz)))
        return float(self.volts_peak[i]), float(self.dbm[i])

    def top(self, n: int = 10) -> list[dict[str, float]]:
        order = np.argsort(self.dbm)[::-1][:n]
        return [
            {"freq_hz": float(self.freqs_hz[i]), "dbm": float(self.dbm[i])}
            for i in sorted(order, key=lambda k: self.freqs_hz[k])
        ]


@dataclass
class HBResult:
    spectrum: HBSpectrum
    fundamentals_hz: list[float]
    fundamental_dbm: list[float]
    input_power_dbm: float
    netlist_path: Path
    output_path: Path
    im3_dbm: float | None = None
    im3_freqs_hz: list[float] = field(default_factory=list)
    oip3_dbm: float | None = None
    iip3_dbm: float | None = None
    gain_db: float | None = None


def build_hb_netlist(
    dut_lines: list[str],
    *,
    fundamentals_hz: list[float],
    harmonics: int,
    input_power_dbm: float,
    in_node: str = "in",
    out_node: str = "out",
    z0: float = 50.0,
    title: str = "Harmonic balance (mcp-qucs-s)",
) -> str:
    """Wrap a DUT netlist in tone sources, a matched load and a ``.HB`` analysis.

    ``dut_lines`` are raw SPICE lines — devices, ``.SUBCKT``/``.MODEL`` cards,
    whatever the circuit needs — referring to ``in_node`` and ``out_node``.
    Sources, termination and analysis directives are added here so the caller
    cannot get the ``NUMFREQ``-per-tone rule wrong.
    """
    if not fundamentals_hz:
        raise ValueError("At least one fundamental frequency is required.")
    if len(fundamentals_hz) > 2:
        raise NotImplementedError(
            f"Only single- and two-tone harmonic balance are supported; got "
            f"{len(fundamentals_hz)} fundamentals. Multi-tone HB is out of scope."
        )
    if any(f <= 0 for f in fundamentals_hz):
        raise ValueError(f"Fundamentals must be positive: {fundamentals_hz}")
    if harmonics < 1:
        raise ValueError(f"harmonics must be >= 1; got {harmonics}")
    if len(fundamentals_hz) == 2 and math.isclose(
        fundamentals_hz[0], fundamentals_hz[1], rel_tol=_BIN_RTOL
    ):
        raise ValueError(
            "The two fundamentals are identical, so there are no intermodulation "
            "products to compute. Use a single tone, or separate them."
        )

    amp = dbm_to_source_amplitude(input_power_dbm, z0)
    lines = [title]

    # Tone sources in series so both drive the same port.
    if len(fundamentals_hz) == 1:
        lines.append(f"Vs1 src 0 SIN(0 {amp:.10g} {fundamentals_hz[0]:.10g})")
    else:
        lines.append(f"Vs1 src n_tone SIN(0 {amp:.10g} {fundamentals_hz[0]:.10g})")
        lines.append(f"Vs2 n_tone 0 SIN(0 {amp:.10g} {fundamentals_hz[1]:.10g})")

    lines.append(f"Rs src {in_node} {z0:.10g}")
    lines.extend(dut_lines)
    lines.append(f"RL {out_node} 0 {z0:.10g}")

    tones = " ".join(f"{f:.10g}" for f in fundamentals_hz)
    numfreq = ",".join(str(int(harmonics)) for _ in fundamentals_hz)
    lines.append(f".HB {tones}")
    lines.append(f".OPTIONS HBINT NUMFREQ={numfreq}")
    lines.append(f".PRINT HB_FD V({out_node})")
    lines.append(".END")
    return "\n".join(lines) + "\n"


def parse_hb_fd(prn_path: str | Path, *, z0: float = 50.0) -> HBSpectrum:
    """Parse ``<netlist>.HB.FD.prn`` into a single-sided spectrum.

    Xyce prints a two-sided spectrum; the physical amplitude at a positive
    frequency is twice the magnitude of its row. DC is not doubled.
    """
    path = Path(prn_path)
    text = path.read_text(encoding="utf-8", errors="replace")

    freqs: list[float] = []
    reals: list[float] = []
    imags: list[float] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        if not parts[0].lstrip("-").isdigit():
            continue  # header or trailer
        try:
            freqs.append(float(parts[1]))
            reals.append(float(parts[2]))
            imags.append(float(parts[3]))
        except ValueError:
            continue

    if not freqs:
        raise ValueError(
            f"No harmonic-balance data rows in {path}. Xyce may have aborted; "
            "check the .log beside it."
        )

    f = np.asarray(freqs)
    mag = np.abs(np.asarray(reals) + 1j * np.asarray(imags))

    keep = f >= -abs(f).max() * _BIN_RTOL  # positive half, including DC
    f_pos = f[keep]
    v = mag[keep] * 2.0
    v[np.abs(f_pos) <= abs(f).max() * _BIN_RTOL] /= 2.0  # DC is not doubled

    order = np.argsort(f_pos)
    f_pos, v = f_pos[order], v[order]
    return HBSpectrum(freqs_hz=f_pos, volts_peak=v, dbm=np.asarray(volts_to_dbm(v, z0)))


def run_xyce(netlist_text: str, *, workdir: Path, timeout_sec: float = 300.0) -> Path:
    """Run Xyce on ``netlist_text`` and return the ``.HB.FD.prn`` path.

    Xyce writes results beside the netlist, so ``workdir`` must be writable
    and is best kept per-run.
    """
    exe = find_xyce()
    if exe is None:
        raise RuntimeError(
            "Xyce not found. Build it from source (see docs/installation.md); "
            "Sandia's Linux binaries are RHEL RPMs that do not work on "
            "Debian/Ubuntu, and they no longer ship open-source builds."
        )

    workdir.mkdir(parents=True, exist_ok=True)
    netlist_path = workdir / "hb.cir"
    netlist_path.write_text(netlist_text, encoding="utf-8")

    proc = subprocess.run(
        [str(exe), str(netlist_path)],
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
        cwd=str(workdir),
    )
    log_path = workdir / "hb.log"
    log_path.write_text(
        f"$ {exe} {netlist_path}\nreturncode: {proc.returncode}\n"
        f"=== stdout ===\n{proc.stdout}\n=== stderr ===\n{proc.stderr}\n",
        encoding="utf-8",
    )

    prn = netlist_path.with_suffix(".cir.HB.FD.prn")
    if not prn.is_file():
        candidates = sorted(workdir.glob("*.HB.FD.prn"))
        if candidates:
            prn = candidates[0]
        else:
            tail = (proc.stdout + proc.stderr)[-800:]
            raise RuntimeError(
                f"Xyce produced no HB frequency-domain output. "
                f"returncode={proc.returncode}. Full log at {log_path}.\n{tail}"
            )
    return prn


def analyze(
    dut_lines: list[str],
    *,
    fundamentals_hz: list[float],
    harmonics: int = 5,
    input_power_dbm: float = -20.0,
    in_node: str = "in",
    out_node: str = "out",
    z0: float = 50.0,
    workdir: Path | None = None,
    timeout_sec: float = 300.0,
) -> HBResult:
    """Run one harmonic-balance point and derive the usual RF figures."""
    netlist = build_hb_netlist(
        dut_lines,
        fundamentals_hz=fundamentals_hz,
        harmonics=harmonics,
        input_power_dbm=input_power_dbm,
        in_node=in_node,
        out_node=out_node,
        z0=z0,
    )
    run_dir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="mcp-hb-"))
    prn = run_xyce(netlist, workdir=run_dir, timeout_sec=timeout_sec)
    spectrum = parse_hb_fd(prn, z0=z0)

    fund_dbm = [spectrum.at(f)[1] for f in fundamentals_hz]
    result = HBResult(
        spectrum=spectrum,
        fundamentals_hz=list(fundamentals_hz),
        fundamental_dbm=fund_dbm,
        input_power_dbm=input_power_dbm,
        netlist_path=run_dir / "hb.cir",
        output_path=prn,
        gain_db=fund_dbm[0] - input_power_dbm if fund_dbm else None,
    )

    if len(fundamentals_hz) == 2:
        f1, f2 = fundamentals_hz
        lo, hi = 2.0 * f1 - f2, 2.0 * f2 - f1
        result.im3_freqs_hz = [lo, hi]
        im3_levels = [spectrum.at(lo)[1], spectrum.at(hi)[1]]
        # Use the stronger sideband: it is the one that sets the spec.
        im3 = max(im3_levels)
        result.im3_dbm = im3

        # Single-point third-order extrapolation. Valid only while the
        # products still follow their 3:1 slope, i.e. well below compression.
        p_fund = max(fund_dbm)
        delta = p_fund - im3
        result.oip3_dbm = p_fund + delta / 2.0
        result.iip3_dbm = input_power_dbm + delta / 2.0

    return result


def sweep_compression(
    dut_lines: list[str],
    *,
    fundamental_hz: float,
    input_powers_dbm: list[float],
    harmonics: int = 5,
    in_node: str = "in",
    out_node: str = "out",
    z0: float = 50.0,
    timeout_sec: float = 300.0,
) -> dict[str, object]:
    """Sweep drive level and locate the 1 dB gain-compression point.

    Small-signal gain is taken from the lowest drive level swept, so that
    point must genuinely be in the linear region — otherwise P1dB is measured
    against an already-compressed reference and reads high.
    """
    if len(input_powers_dbm) < 2:
        raise ValueError("Need at least two drive levels to find compression.")

    powers = sorted(input_powers_dbm)
    pin: list[float] = []
    pout: list[float] = []
    for p in powers:
        res = analyze(
            dut_lines,
            fundamentals_hz=[fundamental_hz],
            harmonics=harmonics,
            input_power_dbm=p,
            in_node=in_node,
            out_node=out_node,
            z0=z0,
            timeout_sec=timeout_sec,
        )
        pin.append(p)
        pout.append(res.fundamental_dbm[0])

    small_signal_gain = pout[0] - pin[0]
    gain = [o - i for o, i in zip(pout, pin, strict=True)]
    compression = [small_signal_gain - g for g in gain]

    p1db_in: float | None = None
    for i in range(1, len(compression)):
        if compression[i] >= 1.0 >= compression[i - 1]:
            span = compression[i] - compression[i - 1]
            frac = 0.0 if span == 0 else (1.0 - compression[i - 1]) / span
            p1db_in = pin[i - 1] + frac * (pin[i] - pin[i - 1])
            break

    return {
        "input_dbm": pin,
        "output_dbm": pout,
        "gain_db": gain,
        "compression_db": compression,
        "small_signal_gain_db": small_signal_gain,
        "p1db_in_dbm": p1db_in,
        "p1db_out_dbm": (p1db_in + small_signal_gain - 1.0) if p1db_in is not None else None,
    }
