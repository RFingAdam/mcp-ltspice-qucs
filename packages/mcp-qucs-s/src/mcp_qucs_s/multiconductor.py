"""Exact analysis of same-velocity TEM coupled-line arrays.

The 2N-port admittance matrix of an N-line array with characteristic-
admittance matrix ``Y_c`` and common electrical length θ is

    [ −j·cotθ·Y_c    +j·cscθ·Y_c ]
    [ +j·cscθ·Y_c    −j·cotθ·Y_c ]

— **linear in Y_c**, which is the whole trick: a tridiagonal ``Y_c``
decomposes into per-line stub terms and per-coupling K-blocks
``y_m·[[1,−1],[−1,1]]``, so the array is *exactly* a parallel network of
ordinary two-port lines (one stub per line, admittance
``Y_ii − Σ|mutuals|``) plus floating four-port connector lines (one per
coupling, admittance ``y_m``). Probe-verified against qucsator: TLIN
stubs + a TLIN4P connector reproduce the CTLIN coupled-line BPF section
at 0.0000 mdB, so any array handled here is also netlistable in
qucsator exactly (see ``generate_interdigital_netlist``).

:func:`segmented_array_sparams` stacks commensurate array segments
(needed for tapped feeds) into one nodal admittance system and reduces
it to S-parameters exactly. The interdigital pair-resonance split is
closed form — ``det = 0`` gives ``cosθ = ±y_m/Y_r`` — exposed as
:func:`interdigital_pair_k` / :func:`mutual_for_k`.
"""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import brentq

Termination = Literal["short", "open", "port"]


def interdigital_pair_k(r: float) -> float:
    """Coupling coefficient of two coupled λ/4 lines terminated
    interdigitally (shorted at opposite ends), for ``r = y_m / Y_r``.

    The resonance condition of the terminated pair is closed form:
    ``cosθ = ±r``, so the split resonances sit at
    ``f_l/f0 = arccos(r)/(π/2)`` and ``f_h = 2·f0 − f_l``, and
    ``k = (f_h² − f_l²)/(f_h² + f_l²)``.
    """
    if not 0.0 <= r < 1.0:
        raise ValueError(f"y_m/Y_r must be in [0, 1); got {r}")
    fl = math.acos(r) / (math.pi / 2.0)
    fh = 2.0 - fl
    return (fh * fh - fl * fl) / (fh * fh + fl * fl)


def mutual_for_k(k_target: float) -> float:
    """Invert :func:`interdigital_pair_k`: coupling coefficient →
    ``y_m/Y_r``."""
    if not 0.0 < k_target < interdigital_pair_k(0.999):
        raise ValueError(f"k must be in (0, {interdigital_pair_k(0.999):.3f}); got {k_target}")
    return float(brentq(lambda r: interdigital_pair_k(r) - k_target, 1e-12, 0.999, xtol=1e-14))


def segmented_array_sparams(
    y_c: NDArray[np.float64],
    freq_hz: NDArray[np.float64],
    f_ref_hz: float,
    *,
    segments_deg: list[float],
    bottom: list[str],
    top: list[str],
    ports: list[tuple[int, int]] | None = None,
    z0_system: float = 50.0,
) -> NDArray[np.complex128]:
    """Exact S-parameters of a stack of commensurate TEM array segments.

    Nodes live on levels 0..len(segments) (level 0 = bottom); node
    (level, line) is where line ``line`` crosses that boundary. Each
    segment contributes the 2N-port blocks above with
    ``θ_s(f) = θ_s,ref · f/f_ref``.

    ``bottom`` / ``top`` give one termination per line (``"short"``,
    ``"open"`` or ``"port"``); ``ports`` adds interior tap ports as
    (level, line) pairs. Port ordering: bottom ports by line index,
    then the ``ports`` list in order, then top ports by line index.

    Returns S of shape (npoints, nports, nports). Frequencies where a
    segment hits θ = m·π (the commensurate-line poles) are filled with
    the fully-reflective limit.
    """
    y_c = np.asarray(y_c, dtype=np.float64)
    n_lines = y_c.shape[0]
    if y_c.shape != (n_lines, n_lines):
        raise ValueError(f"y_c must be square; got {y_c.shape}")
    if len(bottom) != n_lines or len(top) != n_lines:
        raise ValueError("bottom/top must carry one termination per line")
    n_seg = len(segments_deg)
    if n_seg < 1 or any(t <= 0 for t in segments_deg):
        raise ValueError("segments_deg must be a non-empty list of positive angles")

    n_levels = n_seg + 1

    def node(level: int, line: int) -> int:
        return level * n_lines + line

    port_nodes: list[int] = [node(0, i) for i in range(n_lines) if bottom[i] == "port"]
    for level, line in ports or []:
        if not 0 <= level <= n_seg or not 0 <= line < n_lines:
            raise ValueError(f"port ({level}, {line}) is outside the node grid")
        port_nodes.append(node(level, line))
    port_nodes += [node(n_seg, i) for i in range(n_lines) if top[i] == "port"]
    if len(set(port_nodes)) != len(port_nodes):
        raise ValueError("duplicate port nodes")

    short_nodes = {node(0, i) for i in range(n_lines) if bottom[i] == "short"}
    short_nodes |= {node(n_seg, i) for i in range(n_lines) if top[i] == "short"}
    if short_nodes & set(port_nodes):
        raise ValueError("a node cannot be both a short and a port")

    total = n_levels * n_lines
    keep = [n for n in range(total) if n not in short_nodes]
    kept_index = {n: i for i, n in enumerate(keep)}
    p_idx = [kept_index[n] for n in port_nodes]
    e_idx = [i for i in range(len(keep)) if i not in set(p_idx)]

    theta_ref = np.radians(np.asarray(segments_deg, dtype=np.float64))
    n_ports = len(port_nodes)
    s_out = np.zeros((freq_hz.size, n_ports, n_ports), dtype=np.complex128)
    eye_p = np.eye(n_ports, dtype=np.complex128)

    for fi, f in enumerate(np.asarray(freq_hz, dtype=np.float64)):
        y_full = np.zeros((total, total), dtype=np.complex128)
        singular = False
        for s in range(n_seg):
            theta = theta_ref[s] * f / f_ref_hz
            sin_t = math.sin(theta)
            if abs(sin_t) < 1e-12:
                singular = True
                break
            cot = math.cos(theta) / sin_t
            csc = 1.0 / sin_t
            a = slice(s * n_lines, (s + 1) * n_lines)
            b = slice((s + 1) * n_lines, (s + 2) * n_lines)
            y_full[a, a] += -1j * cot * y_c
            y_full[b, b] += -1j * cot * y_c
            y_full[a, b] += 1j * csc * y_c
            y_full[b, a] += 1j * csc * y_c
        if singular:
            s_out[fi] = -eye_p
            continue

        y_kept = y_full[np.ix_(keep, keep)]
        y_pp = y_kept[np.ix_(p_idx, p_idx)]
        if e_idx:
            y_pe = y_kept[np.ix_(p_idx, e_idx)]
            y_ep = y_kept[np.ix_(e_idx, p_idx)]
            y_ee = y_kept[np.ix_(e_idx, e_idx)]
            try:
                y_red = y_pp - y_pe @ np.linalg.solve(y_ee, y_ep)
            except np.linalg.LinAlgError:
                s_out[fi] = -eye_p
                continue
        else:
            y_red = y_pp

        try:
            s_mat = np.linalg.solve(eye_p + z0_system * y_red, eye_p - z0_system * y_red)
        except np.linalg.LinAlgError:
            s_out[fi] = -eye_p
            continue
        s_out[fi] = np.where(np.isfinite(s_mat), s_mat, -eye_p)

    return s_out
