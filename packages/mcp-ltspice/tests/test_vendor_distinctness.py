"""Tests verifying that vendor catalogues are genuinely distinct, not aliases.

Background: prior versions had `JOHANSON_L = COILCRAFT_0402HP.copy()` and
`TDK_MLG = COILCRAFT_0402HP.copy()` — these aliases mislead users into
thinking they have multi-vendor coverage when in fact only one set of
parasitic data backed all three. The aliases were replaced with
distinct nominal datasheet values for the Johanson L-07W and TDK
MLK1005S series.
"""

from __future__ import annotations

import pytest

from mcp_ltspice.vendor_models import (
    list_vendor_parts,
    lookup_part,
)


VENDOR_PAIRS_TO_CHECK = [
    ("johanson_l", "coilcraft_0402hp"),
    ("tdk_mlg", "coilcraft_0402hp"),
    ("johanson_l", "tdk_mlg"),
]


@pytest.mark.parametrize("a, b", VENDOR_PAIRS_TO_CHECK)
def test_value_lists_or_srfs_differ(a: str, b: str):
    """Two catalogues are 'distinct' if either their value lists differ
    OR (for shared values) their SRFs differ. Pure aliases would match on both."""
    values_a = set(list_vendor_parts(a))
    values_b = set(list_vendor_parts(b))

    if values_a != values_b:
        return  # different value sets — automatically distinct

    # Same value set; SRFs at common values must differ for at least one entry.
    common = values_a & values_b
    assert any(
        lookup_part(a, v, kind="L").srf_hz != lookup_part(b, v, kind="L").srf_hz
        for v in common
    ), f"Vendors {a!r} and {b!r} have identical value lists AND identical SRFs — they are effectively the same catalogue."


def test_johanson_extends_to_higher_inductances():
    """Johanson L-07W reaches 39 nH; Coilcraft 0402HP tops out at 22 nH."""
    j = list_vendor_parts("johanson_l")
    c = list_vendor_parts("coilcraft_0402hp")
    assert max(j) > max(c), (
        f"Expected Johanson L-07W to extend higher than Coilcraft 0402HP; "
        f"got max(johanson_l)={max(j)*1e9:.0f}nH vs max(coilcraft_0402hp)={max(c)*1e9:.0f}nH"
    )


def test_tdk_extends_to_lower_inductances():
    """TDK MLK1005S reaches sub-1 nH (0.6 nH); Coilcraft 0402HP starts at 1 nH."""
    t = list_vendor_parts("tdk_mlg")
    c = list_vendor_parts("coilcraft_0402hp")
    assert min(t) < min(c), (
        f"Expected TDK MLK1005S to extend lower than Coilcraft 0402HP; "
        f"got min(tdk_mlg)={min(t)*1e9:.2f}nH vs min(coilcraft_0402hp)={min(c)*1e9:.2f}nH"
    )


def test_no_two_inductor_catalogues_are_identical_objects():
    """Each vendor catalogue must be its own dict object, not a shared reference.
    A shared reference would mean modifying one mutates all."""
    from mcp_ltspice.vendor_models import (
        COILCRAFT_0402HP,
        COILCRAFT_0603CS,
        JOHANSON_L,
        TDK_MLG,
    )
    catalogues = [COILCRAFT_0402HP, COILCRAFT_0603CS, JOHANSON_L, TDK_MLG]
    ids_seen = set()
    for cat in catalogues:
        assert id(cat) not in ids_seen, (
            "Two vendor catalogues share the same dict object — modifying one would mutate all."
        )
        ids_seen.add(id(cat))
