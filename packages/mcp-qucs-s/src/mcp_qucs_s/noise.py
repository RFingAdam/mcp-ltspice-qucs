"""Noise-parameter extraction from a Qucs-S noise analysis.

Adding ``Noise="yes"`` to a ``.SP`` analysis makes qucsator emit the four
classical noise parameters alongside the S-matrix:

===========  ====================================================
``F``        noise factor with the port reference impedance (NF50)
``Fmin``     minimum achievable noise factor
``Sopt``     source reflection coefficient giving ``Fmin`` (Γopt)
``Rn``       equivalent noise resistance
===========  ====================================================

Two conventions were established against qucsator-RF 1.0.7 rather than
taken on trust, because getting either wrong yields plausible numbers that
are quietly wrong:

- **``F`` and ``Fmin`` are linear noise factors, not dB.**
- **``Rn`` is in absolute ohms, not normalised to Z₀.** Evaluating the
  classical noise formula at Γs = 0 on an asymmetric lossy two-port
  reproduces the reported ``F`` to 4e-16 with ohms, and is off by 0.01 if
  ``Rn`` is treated as normalised.

Noise temperature is a **per-component** property in Qucs, not an analysis
setting: ``.SP ... Temp="16.85"`` does not change device noise. A passive
network's noise figure equals its insertion loss only at the IEEE reference
temperature T₀ = 290 K = 16.85 °C, and Qucs defaults components to
26.85 °C, so a 10 dB pad reads 10.13 dB out of the box —
``1 + (L-1)·T/T₀`` with T = 300 K. That is correct physics, not an error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from mcp_qucs_s.sparams import parse_qucs_dat
from rf_mcp_common.logging import get_logger

log = get_logger("mcp_qucs_s.noise")

#: IEEE reference temperature for noise figure, in Celsius (290 K).
T0_CELSIUS = 16.85

_R_LINE = re.compile(r"^\s*R:")


@dataclass
class NoiseParameters:
    """Per-frequency classical noise parameters."""

    freq_hz: NDArray[np.float64]
    nf_db: NDArray[np.float64]
    nfmin_db: NDArray[np.float64]
    gamma_opt: NDArray[np.complex128]
    rn_ohm: NDArray[np.float64]
    z0: float = 50.0

    def nf_db_at_source(self, gamma_s: complex | NDArray[np.complex128]) -> NDArray[np.float64]:
        """Noise figure for an arbitrary source reflection coefficient.

        The classical two-port relation::

            F(Γs) = Fmin + (4·Rn/Z0)·|Γs − Γopt|² / ((1−|Γs|²)·|1+Γopt|²)

        This is the payoff for LNA design: it says what the noise figure
        becomes once a real matching network is placed in front, which is
        never quite Γopt.
        """
        gs = np.asarray(gamma_s, dtype=np.complex128)
        if np.any(np.abs(gs) >= 1.0):
            raise ValueError(
                "|gamma_s| must be < 1; a passive source cannot reflect more "
                "power than it receives."
            )
        fmin = 10.0 ** (self.nfmin_db / 10.0)
        num = 4.0 * (self.rn_ohm / self.z0) * np.abs(gs - self.gamma_opt) ** 2
        den = (1.0 - np.abs(gs) ** 2) * np.abs(1.0 + self.gamma_opt) ** 2
        return np.asarray(10.0 * np.log10(fmin + num / den))

    def at(self, freq_hz: float) -> dict[str, float]:
        """The parameter set in the bin nearest ``freq_hz``."""
        i = int(np.argmin(np.abs(self.freq_hz - freq_hz)))
        return {
            "freq_hz": float(self.freq_hz[i]),
            "nf_db": float(self.nf_db[i]),
            "nfmin_db": float(self.nfmin_db[i]),
            "gamma_opt_mag": float(np.abs(self.gamma_opt[i])),
            "gamma_opt_deg": float(np.degrees(np.angle(self.gamma_opt[i]))),
            "rn_ohm": float(self.rn_ohm[i]),
        }

    def as_rows(self) -> list[dict[str, float]]:
        return [self.at(f) for f in self.freq_hz]


def build_noise_netlist(
    dut_lines: list[str],
    *,
    f_start_hz: float,
    f_stop_hz: float,
    points: int = 21,
    z0: float = 50.0,
    sweep: str = "log",
    temp_c: float | None = T0_CELSIUS,
    port1_node: str = "_p1",
    port2_node: str = "_p2",
    title: str = "Noise analysis (mcp-qucs-s)",
) -> str:
    """Wrap a DUT in ports and a noise-enabled ``.SP`` analysis.

    ``temp_c`` appends ``Temp="…"`` to every ``R:`` line that does not
    already carry one, defaulting to the IEEE reference 16.85 °C so a
    passive network's noise figure equals its loss. Only resistor lines are
    touched — silently rewriting arbitrary device cards would be worse than
    leaving them alone. Pass ``None`` to use whatever the DUT declares, and
    set ``Temp`` yourself on active devices.
    """
    if f_stop_hz <= f_start_hz:
        raise ValueError(f"f_stop_hz ({f_stop_hz}) must exceed f_start_hz ({f_start_hz})")
    if points < 1:
        raise ValueError(f"points must be >= 1; got {points}")
    if z0 <= 0:
        raise ValueError(f"z0 must be positive; got {z0}")
    if not dut_lines:
        raise ValueError("Cannot analyse an empty circuit.")

    body: list[str] = []
    for line in dut_lines:
        if temp_c is not None and _R_LINE.match(line) and "Temp=" not in line:
            line = f'{line.rstrip()} Temp="{temp_c:.10g}"'
        body.append(line)

    port_z = f'Z="{z0:.10g} Ohm"'
    header = [
        f"# {title}",
        f'Pac:P1 {port1_node} gnd Num="1" {port_z} P="0 dBm" f="1 GHz"',
        f'Pac:P2 {port2_node} gnd Num="2" {port_z} P="0 dBm" f="1 GHz"',
    ]
    analysis = [
        f'.SP:SP1 Type="{sweep}" Start="{f_start_hz:.10g}" '
        f'Stop="{f_stop_hz:.10g}" Points="{int(points)}" Noise="yes"'
    ]
    return "\n".join(header + body + analysis) + "\n"


def parse_noise_parameters(dat_path: str | Path, *, z0: float = 50.0) -> NoiseParameters:
    """Read ``F``, ``Fmin``, ``Sopt`` and ``Rn`` from a qucsator dataset."""
    data = parse_qucs_dat(dat_path)

    missing = [k for k in ("frequency", "F", "Fmin", "Sopt", "Rn") if k not in data]
    if missing:
        raise ValueError(
            f"{dat_path} has no noise dataset (missing {missing}). Add "
            'Noise="yes" to the .SP analysis. Found: ' + ", ".join(sorted(data))
        )

    freq = np.real(np.asarray(data["frequency"], dtype=np.complex128))
    f_lin = np.real(np.asarray(data["F"], dtype=np.complex128))
    fmin_lin = np.real(np.asarray(data["Fmin"], dtype=np.complex128))
    rn = np.real(np.asarray(data["Rn"], dtype=np.complex128))
    gamma_opt = np.asarray(data["Sopt"], dtype=np.complex128)

    # Qucs reports linear noise factors. A factor below 1 would mean a
    # network that improves SNR, which is not physical.
    if np.any(f_lin < 1.0 - 1e-9) or np.any(fmin_lin < 1.0 - 1e-9):
        log.warning(
            "Noise factor below 1 in %s (min F=%.6g, min Fmin=%.6g); the dataset "
            "may not be a linear noise factor as assumed.",
            dat_path,
            float(f_lin.min()),
            float(fmin_lin.min()),
        )

    return NoiseParameters(
        freq_hz=freq,
        nf_db=np.asarray(10.0 * np.log10(np.maximum(f_lin, 1e-30))),
        nfmin_db=np.asarray(10.0 * np.log10(np.maximum(fmin_lin, 1e-30))),
        gamma_opt=gamma_opt,
        rn_ohm=rn,
        z0=z0,
    )


def analyze_noise(
    dut_lines: list[str],
    *,
    f_start_hz: float,
    f_stop_hz: float,
    points: int = 21,
    z0: float = 50.0,
    temp_c: float | None = T0_CELSIUS,
    workdir: Path | None = None,
    timeout_sec: float = 300.0,
) -> NoiseParameters:
    """Generate, run and parse a Qucs-S noise analysis."""
    import tempfile

    from mcp_qucs_s.runner import run_qucs

    netlist = build_noise_netlist(
        dut_lines,
        f_start_hz=f_start_hz,
        f_stop_hz=f_stop_hz,
        points=points,
        z0=z0,
        temp_c=temp_c,
    )
    run_dir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="mcp-noise-"))
    run_dir.mkdir(parents=True, exist_ok=True)
    net_path = run_dir / "noise.net"
    net_path.write_text(netlist, encoding="utf-8")

    result = run_qucs(net_path, timeout_sec=timeout_sec)
    return parse_noise_parameters(result.output_path, z0=z0)
