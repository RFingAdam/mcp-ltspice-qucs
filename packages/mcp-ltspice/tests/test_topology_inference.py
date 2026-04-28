"""Tests for `infer_transmission_zeros` and the auto-inference default
on `components_dict_to_elements`.

Background: prior to this change, the default
`transmission_zeros=False` silently treated even-indexed Cs as plain
shunt caps. For an elliptic ladder where Lk + Ck (even k) form shunt-LC
traps, forgetting the flag produced wrong S-parameters with no
warning. The new default is to infer the topology from the dict; an
explicit flag that disagrees with the inference now emits a warning.
"""

from __future__ import annotations

import warnings

import numpy as np

from mcp_ltspice.extract import (
    components_dict_to_elements,
    infer_transmission_zeros,
    ladder_sparams_from_components,
)

# Butterworth/Chebyshev 5th-order series-first: L1, C2, L3, C4, L5
BUTTER_LIKE = {
    "L1": 6.2e-9,
    "C2": 5.1e-12,
    "L3": 8.6e-9,
    "C4": 5.1e-12,
    "L5": 6.2e-9,
}

# Elliptic 5th-order: L1, L2+C2 (trap), L3, L4+C4 (trap), L5
ELLIPTIC_LIKE = {
    "L1": 4.0e-9,
    "L2": 2.0e-9,  # paired with C2 → trap
    "C2": 8.0e-12,
    "L3": 6.0e-9,
    "L4": 1.5e-9,  # paired with C4 → trap
    "C4": 4.0e-12,
    "L5": 4.0e-9,
}


class TestInferTransmissionZeros:
    def test_butter_returns_false(self):
        assert infer_transmission_zeros(BUTTER_LIKE) is False

    def test_elliptic_returns_true(self):
        assert infer_transmission_zeros(ELLIPTIC_LIKE) is True

    def test_only_odd_indices_returns_false(self):
        # Lone L1, L3, L5 — no traps
        assert infer_transmission_zeros({"L1": 1e-9, "L3": 2e-9, "L5": 1e-9}) is False

    def test_even_index_lone_l_returns_false(self):
        # L2 alone (no matching C2) — not a trap
        assert infer_transmission_zeros({"L1": 1e-9, "L2": 2e-9, "L3": 1e-9}) is False

    def test_even_index_lone_c_returns_false(self):
        # C2 alone (no matching L2) — Butterworth-ish shunt cap
        assert infer_transmission_zeros({"L1": 1e-9, "C2": 2e-12, "L3": 1e-9}) is False


class TestAutoInferenceDefault:
    """When transmission_zeros is omitted, behaviour matches an explicit
    inference choice."""

    def test_butter_components_default_matches_explicit_false(self):
        auto = components_dict_to_elements(BUTTER_LIKE)
        explicit = components_dict_to_elements(BUTTER_LIKE, transmission_zeros=False)
        assert auto == explicit

    def test_elliptic_components_default_matches_explicit_true(self):
        auto = components_dict_to_elements(ELLIPTIC_LIKE)
        explicit = components_dict_to_elements(ELLIPTIC_LIKE, transmission_zeros=True)
        assert auto == explicit

    def test_elliptic_default_produces_traps(self):
        elements = components_dict_to_elements(ELLIPTIC_LIKE)
        kinds = [e[0] for e in elements]
        assert "shunt_lc_trap" in kinds
        # Two trap pairs in 5th-order elliptic
        assert kinds.count("shunt_lc_trap") == 2

    def test_butter_default_produces_no_traps(self):
        elements = components_dict_to_elements(BUTTER_LIKE)
        kinds = [e[0] for e in elements]
        assert "shunt_lc_trap" not in kinds


class TestExplicitFlagDisagreement:
    """Explicit flag disagreeing with inference should warn but still honour the flag."""

    def test_explicit_false_on_elliptic_warns(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            components_dict_to_elements(ELLIPTIC_LIKE, transmission_zeros=False)
        assert any(
            issubclass(rec.category, RuntimeWarning) and "elliptic topology" in str(rec.message)
            for rec in w
        )

    def test_explicit_true_on_butter_warns(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            components_dict_to_elements(BUTTER_LIKE, transmission_zeros=True)
        assert any(
            issubclass(rec.category, RuntimeWarning)
            and "no even-indexed L+C pairs" in str(rec.message)
            for rec in w
        )

    def test_explicit_matches_inference_no_warning(self):
        """No warning when explicit flag agrees with auto-inference."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            components_dict_to_elements(ELLIPTIC_LIKE, transmission_zeros=True)
            components_dict_to_elements(BUTTER_LIKE, transmission_zeros=False)
        runtime_warnings = [rec for rec in w if issubclass(rec.category, RuntimeWarning)]
        assert runtime_warnings == []


class TestSpRegression:
    """Auto-inference on elliptic components must produce the SAME S-parameters
    as the (correct) explicit transmission_zeros=True path. This is the bug we
    hit: forgetting the flag produced wrong S-params; auto-inference fixes it.
    """

    def test_s_params_match_between_default_and_explicit_true(self):
        f = np.geomspace(100e6, 5e9, 401)
        elements_auto = components_dict_to_elements(ELLIPTIC_LIKE)
        elements_explicit = components_dict_to_elements(ELLIPTIC_LIKE, transmission_zeros=True)
        s_auto = ladder_sparams_from_components(elements_auto, f, z0=50.0)
        s_explicit = ladder_sparams_from_components(elements_explicit, f, z0=50.0)
        assert np.allclose(s_auto, s_explicit)

    def test_s_params_differ_when_forced_wrong(self):
        """Sanity: forcing transmission_zeros=False on elliptic components
        gives a different (and incorrect) S-parameter result. This proves
        the auto-inference is doing real work."""
        f = np.geomspace(100e6, 5e9, 401)
        elements_correct = components_dict_to_elements(ELLIPTIC_LIKE)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            elements_wrong = components_dict_to_elements(ELLIPTIC_LIKE, transmission_zeros=False)
        s_correct = ladder_sparams_from_components(elements_correct, f, z0=50.0)
        s_wrong = ladder_sparams_from_components(elements_wrong, f, z0=50.0)
        # The two interpretations should produce visibly different S21 magnitudes
        # somewhere in the sweep (definitionally — different topologies).
        assert not np.allclose(s_correct[:, 1, 0], s_wrong[:, 1, 0], rtol=0.1)
