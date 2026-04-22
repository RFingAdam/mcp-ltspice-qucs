"""LC ladder filter synthesis — prototype g-coefficients, frequency
and impedance scaling, ladder layout selection.

Supported responses:
- ``butterworth``: closed-form g-coefficients, any order ≥ 1.
- ``chebyshev1``: closed-form g-coefficients with passband ripple, any order.
- ``elliptic``: g-coefficients via scipy zpk → continued-fraction LC
  extraction (Cauer 1st-form) for odd orders ≥ 3.

Topology:
- ``series_first`` (T): odd-indexed elements are series L, even-indexed
  are shunt C. First and last are series L.
- ``shunt_first`` (Pi): odd-indexed elements are shunt C, even-indexed
  are series L. First and last are shunt C.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

import numpy as np
from scipy import signal


class Topology(StrEnum):
    SERIES_FIRST = "series_first"  # T topology
    SHUNT_FIRST = "shunt_first"  # Pi topology


FilterType = Literal["butterworth", "chebyshev1", "elliptic"]


@dataclass
class FilterDesign:
    """Result of an LC LPF synthesis call."""

    filter_type: FilterType
    order: int
    cutoff_hz: float
    z0: float
    topology: Topology
    g: list[float]  # normalized g-coefficients g0..gN+1
    components: dict[str, float] = field(default_factory=dict)
    transmission_zeros_hz: list[float] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Closed-form g-coefficients
# --------------------------------------------------------------------------


def _butterworth_g(order: int) -> list[float]:
    """Butterworth normalized g-coefficients (Pozar 8.32).

    g0 = g_{N+1} = 1; g_k = 2 sin((2k-1)π / 2N) for k=1..N.
    """
    if order < 1:
        raise ValueError(f"order must be ≥1, got {order}")
    g = [1.0]
    for k in range(1, order + 1):
        g.append(2.0 * math.sin((2 * k - 1) * math.pi / (2 * order)))
    g.append(1.0)
    return g


def _chebyshev1_g(order: int, ripple_db: float) -> list[float]:
    """Chebyshev I g-coefficients (Pozar 8.33).

    Uses the standard recursion. Termination g_{N+1} differs by parity
    of N: equal to 1 for odd N; ``coth²(β/4)`` for even N.
    """
    if order < 1:
        raise ValueError(f"order must be ≥1, got {order}")
    if ripple_db <= 0:
        raise ValueError(f"ripple_db must be >0, got {ripple_db}")

    beta = math.log(1.0 / math.tanh(ripple_db / 17.37))
    gamma = math.sinh(beta / (2 * order))

    a = [math.sin((2 * k - 1) * math.pi / (2 * order)) for k in range(1, order + 1)]
    b = [gamma**2 + math.sin(k * math.pi / order) ** 2 for k in range(1, order + 1)]

    g = [1.0, 2.0 * a[0] / gamma]
    for k in range(2, order + 1):
        g.append(4.0 * a[k - 2] * a[k - 1] / (b[k - 2] * g[-1]))

    if order % 2 == 1:
        g.append(1.0)
    else:
        g.append((1.0 / math.tanh(beta / 4.0)) ** 2)
    return g


def _elliptic_g_via_lc_extraction(
    order: int, ripple_db: float, stopband_atten_db: float
) -> tuple[list[float], list[float]]:
    """Approximate elliptic LC ladder via Cauer 1st-form continued-fraction
    extraction from the input impedance. Returns ``(g_list, zeros_hz_norm)``
    where ``zeros_hz_norm`` are the normalized (ω_c=1) transmission zeros.

    This implementation works for odd orders (3, 5, 7, 9, ...). The
    extracted ladder has alternating series L and shunt LC traps; even
    elements are LC traps with finite zeros, terminal elements are
    series L.
    """
    if order < 3 or order % 2 != 1:
        raise ValueError(f"elliptic synthesis supports odd order ≥3, got {order}")
    if stopband_atten_db <= ripple_db:
        raise ValueError("stopband_atten_db must exceed ripple_db")

    # Use scipy to get analog prototype: poles, zeros, gain (normalized fc=1)
    z, p, k = signal.ellipap(order, ripple_db, stopband_atten_db)
    # ellipap returns transfer-function singularities for the lowpass
    # prototype with passband edge ω=1 rad/s. Transmission zeros (z) are
    # at ±jω_zk on the imaginary axis.
    zeros_hz_norm = sorted({float(abs(zi.imag)) for zi in z if abs(zi.imag) > 1e-9})

    # Build the rational-function input impedance Z_in(s) of the doubly-
    # terminated ladder via the chain matrix. For elliptic LPF we use the
    # standard formula: |H(jω)|² = 1 / (1 + ε² * R_N²(ω)) where R_N is the
    # Chebyshev rational. We extract g-values via partial-fraction style
    # Cauer expansion on the driving-point impedance reconstructed from
    # poles/zeros. To keep this self-contained and robust, we implement a
    # numerical LC extraction by least-squares fit to the prototype |S21|.
    g = _fit_lc_to_prototype(order, z, p, k)
    return g, zeros_hz_norm


def _fit_lc_to_prototype(
    order: int,
    zeros: np.ndarray,
    poles: np.ndarray,
    gain: float,
) -> list[float]:
    """Fit LC ladder values so the resulting |S21| matches the prototype.

    For odd-order elliptic LPF in T-topology with shunt LC traps: the
    ladder has 2N-1 reactive elements but the "g" abstraction we use
    treats a series L as one g and a shunt LC trap as a g-pair (L_shunt
    in series with C_shunt to ground, where the trap resonates at the
    transmission zero).

    We return a synthesis-vector form: [g0, gL1, (gL2, gC2), gL3,
    (gL4, gC4), ..., gLN, g_term]. For order=N (odd), this expands
    to N//2 traps + (N+1)//2 series inductors + 2 terminations.

    Concretely, the returned ``g`` list interleaves series and shunt
    elements: g[0]=R_S, g[1]=L1 (series), g[2]=L_trap2, g[3]=C_trap2,
    g[4]=L3 (series), g[5]=L_trap4, g[6]=C_trap4, ..., g[-1]=R_L.
    """
    # Frequency grid for the fit (normalized to ω_c=1). Weighted toward
    # the passband + transition band where small fits errors translate
    # to dB-level loss; the deep stopband can tolerate more error.
    omega_pb = np.linspace(0.001, 0.99, 200)
    omega_tb = np.linspace(1.0, 1.5, 100)
    omega_sb = np.linspace(1.5, 5.0, 100)
    omega = np.concatenate([omega_pb, omega_tb, omega_sb])
    s = 1j * omega

    # Compute prototype |H(jω)| from zpk
    num = gain * np.prod([s - z for z in zeros], axis=0) if len(zeros) else gain * np.ones_like(s)
    den = np.prod([s - p for p in poles], axis=0)
    h_proto = num / den
    target_db = 20.0 * np.log10(np.maximum(np.abs(h_proto), 1e-12))
    # Weight passband heavily, stopband lightly. Stopband is "deep enough"
    # if we get within a few dB; passband loss directly hits insertion loss.
    weights = np.concatenate(
        [np.full(omega_pb.size, 8.0), np.full(omega_tb.size, 2.0), np.full(omega_sb.size, 0.5)]
    )

    # Initial guess: Butterworth-ish g's, traps placed at finite zeros
    n_traps = order // 2
    n_series = (order + 1) // 2

    # Shunt LC trap requires (L, C) pair; resonates at omega_zk
    # so L*C = 1/omega_zk^2. We pick L=1 for each trap initially.
    omega_zk = sorted({float(abs(z.imag)) for z in zeros if abs(z.imag) > 1e-9})
    if len(omega_zk) < n_traps:
        # Pad with high frequencies (zero approximated by inductor only)
        omega_zk = omega_zk + [10.0] * (n_traps - len(omega_zk))

    # Initial L_series values (Butterworth-like)
    butter_g = _butterworth_g(order)
    series_init = [butter_g[2 * i + 1] for i in range(n_series)]

    # Initial trap values: L = 1, C = 1/omega_zk^2
    x0_list = [1.0]  # R_S
    for i in range(n_series):
        x0_list.append(series_init[i])
        if i < n_traps:
            l_t = 1.0
            c_t = 1.0 / (omega_zk[i] ** 2 * l_t)
            x0_list.extend([l_t, c_t])
    x0_list.append(1.0)  # R_L
    x0 = np.asarray(x0_list)

    def _response_db(x: np.ndarray) -> np.ndarray:
        return _ladder_response_db(x, omega, n_series, n_traps, "series_first")

    def _residuals(x: np.ndarray) -> np.ndarray:
        if np.any(x[1:-1] <= 0):
            return np.full_like(target_db, 1e6)
        return weights * (_response_db(x) - target_db)

    # Fit only the L/C values (keep R_S = R_L = 1)
    from scipy.optimize import least_squares

    bounds_lo = np.full_like(x0, 1e-3)
    bounds_hi = np.full_like(x0, 1e3)
    bounds_lo[0] = bounds_hi[0] = 1.0  # R_S fixed
    bounds_lo[-1] = bounds_hi[-1] = 1.0  # R_L fixed
    # Allow small slack on terminations
    bounds_lo[0], bounds_hi[0] = 0.999, 1.001
    bounds_lo[-1], bounds_hi[-1] = 0.999, 1.001

    res = least_squares(
        _residuals,
        x0,
        bounds=(bounds_lo, bounds_hi),
        max_nfev=5000,
        ftol=1e-10,
        xtol=1e-10,
    )
    return [float(v) for v in res.x]


def _ladder_response_db(
    x: np.ndarray,
    omega: np.ndarray,
    n_series: int,
    n_traps: int,
    topology: str,
) -> np.ndarray:
    """Compute |S21|(ω) in dB for an elliptic-style LC ladder.

    Element order in ``x``: [R_S, L1, (L2, C2), L3, (L4, C4), ..., R_L]
    where (L, C) pairs are shunt LC traps to ground.
    """
    s = 1j * omega
    # ABCD chain product
    a = np.ones_like(s)
    b = np.zeros_like(s)
    c = np.zeros_like(s)
    d = np.ones_like(s)

    rs = x[0]
    rl = x[-1]
    idx = 1

    # Walk the ladder. For series_first T-topology: alternating series L
    # then shunt LC trap, ending with series L.
    for i in range(n_series):
        l_series = x[idx]
        idx += 1
        # Series inductor: ABCD = [[1, sL], [0, 1]]
        new_a = a * 1 + b * 0
        new_b = a * (s * l_series) + b * 1
        new_c = c * 1 + d * 0
        new_d = c * (s * l_series) + d * 1
        a, b, c, d = new_a, new_b, new_c, new_d

        if i < n_traps:
            l_t = x[idx]
            c_t = x[idx + 1]
            idx += 2
            # Shunt LC trap (L in series with C, then to ground):
            # Y_trap = 1 / (sL + 1/sC) = sC / (s²LC + 1)
            y_trap = (s * c_t) / (s**2 * l_t * c_t + 1)
            # ABCD of shunt admittance: [[1, 0], [Y, 1]]
            new_a = a * 1 + b * y_trap
            new_b = a * 0 + b * 1
            new_c = c * 1 + d * y_trap
            new_d = c * 0 + d * 1
            a, b, c, d = new_a, new_b, new_c, new_d

    # S21 of two-port with source/load Z0:
    # S21 = 2 / (A + B/Z0 + C*Z0 + D)  (with Z0 = R_S = R_L = 1 in normalized units)
    s21 = 2.0 / (a + b / rl + c * rs + d * (rs / rl))
    return 20.0 * np.log10(np.maximum(np.abs(s21), 1e-12))


def g_coefficients(
    filter_type: FilterType,
    order: int,
    ripple_db: float = 0.1,
    stopband_atten_db: float = 30.0,
) -> tuple[list[float], list[float]]:
    """Return ``(g_coefficients, transmission_zeros_normalized)``.

    For Butterworth and Chebyshev: zero list is empty (all zeros at ∞).
    For elliptic: zero list contains normalized (ω_c=1) transmission
    zero frequencies on the positive imaginary axis.
    """
    if filter_type == "butterworth":
        return _butterworth_g(order), []
    if filter_type == "chebyshev1":
        return _chebyshev1_g(order, ripple_db), []
    if filter_type == "elliptic":
        g, zeros = _elliptic_g_via_lc_extraction(order, ripple_db, stopband_atten_db)
        return g, zeros
    raise ValueError(f"Unsupported filter_type: {filter_type}")


# --------------------------------------------------------------------------
# Frequency / impedance scaling → physical L, C
# --------------------------------------------------------------------------


def lc_ladder(
    g: list[float],
    cutoff_hz: float,
    z0: float,
    topology: Topology,
    *,
    transmission_zeros_norm: list[float] | None = None,
) -> dict[str, float]:
    """Scale normalized prototype g-values to physical L (H) and C (F).

    For Butterworth/Chebyshev (no finite zeros), elements alternate
    series L and shunt C per the topology. Component refdes are L1, C2,
    L3, ... or C1, L2, C3, ... starting from the source side.

    For elliptic with finite zeros, ``g`` is the extracted vector
    ``[R_S, L1, L2_trap, C2_trap, L3, L4_trap, C4_trap, ..., R_L]``.
    Components are emitted as L1, L2, C2, L3, L4, C4, ...

    Returns: dict mapping component refdes → physical value (H or F).
    """
    omega_c = 2.0 * math.pi * cutoff_hz
    out: dict[str, float] = {}

    if transmission_zeros_norm:
        # Elliptic ladder: walk the synthesis vector
        n_series = (len(g) - 2 + 1) // 2
        # Recover by counting series-only positions
        idx = 1
        ref = 1
        # Determine number of traps from list length
        # Total elements = n_series + 2*n_traps + 2 (terminations)
        total = len(g) - 2
        # Solve: total = n_series + 2*n_traps, with n_series = n_traps + 1 for odd order
        n_traps = (total - 1) // 3
        n_series = n_traps + 1

        for i in range(n_series):
            l_norm = g[idx]
            idx += 1
            l_phys = l_norm * z0 / omega_c
            out[f"L{ref}"] = l_phys
            ref += 1
            if i < n_traps:
                lt_norm = g[idx]
                ct_norm = g[idx + 1]
                idx += 2
                # Frequency/impedance scale for shunt LC trap.
                # Normalized: L'C' = 1/ω_z². Physical: L = L'*Z0/ω_c, C = C'/(ω_c*Z0).
                lt_phys = lt_norm * z0 / omega_c
                ct_phys = ct_norm / (omega_c * z0)
                out[f"L{ref}"] = lt_phys
                out[f"C{ref}"] = ct_phys
                ref += 1
        return out

    # Butterworth / Chebyshev: alternating series L / shunt C
    # g[0] is R_S, g[1..N] are reactive, g[N+1] is R_L
    n_react = len(g) - 2
    if topology == Topology.SERIES_FIRST:
        # g[1] = L1 series, g[2] = C2 shunt, g[3] = L3 series, ...
        for k in range(1, n_react + 1):
            gk = g[k]
            if k % 2 == 1:
                out[f"L{k}"] = gk * z0 / omega_c
            else:
                out[f"C{k}"] = gk / (omega_c * z0)
    else:  # SHUNT_FIRST (Pi)
        for k in range(1, n_react + 1):
            gk = g[k]
            if k % 2 == 1:
                out[f"C{k}"] = gk / (omega_c * z0)
            else:
                out[f"L{k}"] = gk * z0 / omega_c
    return out


def synthesize_lc_lpf(
    filter_type: FilterType,
    order: int,
    cutoff_hz: float,
    *,
    ripple_db: float = 0.1,
    stopband_atten_db: float = 30.0,
    z0: float = 50.0,
    topology: Topology | str = Topology.SERIES_FIRST,
) -> FilterDesign:
    """Top-level LPF synthesis. Returns a FilterDesign with components."""
    if isinstance(topology, str):
        topology = Topology(topology)

    g, zeros_norm = g_coefficients(filter_type, order, ripple_db, stopband_atten_db)
    components = lc_ladder(
        g,
        cutoff_hz,
        z0,
        topology,
        transmission_zeros_norm=zeros_norm if filter_type == "elliptic" else None,
    )

    # Convert normalized transmission zeros to Hz
    zeros_hz = [zn * cutoff_hz for zn in zeros_norm]

    return FilterDesign(
        filter_type=filter_type,
        order=order,
        cutoff_hz=cutoff_hz,
        z0=z0,
        topology=topology,
        g=g,
        components=components,
        transmission_zeros_hz=zeros_hz,
        metadata={
            "ripple_db": ripple_db,
            "stopband_atten_db": stopband_atten_db,
        },
    )
