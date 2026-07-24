"""Capability report for the simulator-independent RF analysis server."""

from __future__ import annotations

from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import numpy as np
import scipy
import skrf

SUPPORTED_ANALYSES = (
    "touchstone",
    "network_operations",
    "tdr",
    "eye_diagram",
    "equivalent_circuit_fit",
    "coexistence",
    "emc_estimation",
)


def _distribution_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def analysis_capabilities(*, validate: bool = True) -> dict[str, Any]:
    """Report numerical-stack readiness and optionally run a known answer."""
    result: dict[str, Any] = {
        "backend": "python-numerical-stack",
        "installed": True,
        "launchable": True,
        "validated": False,
        "state": "launchable",
        "versions": {
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "scikit-rf": _distribution_version("scikit-rf") or skrf.__version__,
        },
        "supported_analyses": list(SUPPORTED_ANALYSES),
        "last_probe_time": datetime.now(UTC).isoformat(),
        "diagnostic": "Known-answer validation was not requested.",
        "sandbox_profile": {
            "name": "in_process_no_external_executable",
            "available": True,
            "diagnostic": "Analysis does not launch a circuit simulator.",
        },
    }
    if not validate:
        return result

    # A lossless matched two-port must retain unit forward transmission.
    try:
        frequency = skrf.Frequency.from_f([1e6, 2e6, 3e6], unit="hz")
        s = np.zeros((3, 2, 2), dtype=complex)
        s[:, 0, 1] = 1.0
        s[:, 1, 0] = 1.0
        network = skrf.Network(frequency=frequency, s=s, z0=50.0)
        cascaded = network**network
        if not np.allclose(cascaded.s[:, 1, 0], 1.0, atol=1e-12):
            raise RuntimeError("matched-through cascade did not preserve transmission")
    except Exception as exc:
        result["diagnostic"] = f"known-answer validation failed: {exc}"
        return result

    result["validated"] = True
    result["state"] = "validated"
    result["diagnostic"] = "known-answer network cascade completed"
    return result
