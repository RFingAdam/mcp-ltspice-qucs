"""Package-version helpers backed by installed distribution metadata."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def distribution_version(distribution_name: str) -> str:
    """Return the installed version without duplicating it in source code.

    Packages are normally imported from an installed wheel or editable
    workspace, both of which provide distribution metadata.  The explicit
    fallback keeps source-only tooling importable while making the missing
    metadata visible instead of reporting a plausible but stale release.
    """
    try:
        return version(distribution_name)
    except PackageNotFoundError:
        return "0+unknown"
