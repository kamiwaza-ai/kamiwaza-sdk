"""SemVer 2.0 release identity and precedence for extension catalogs.

Contract: https://semver.org/spec/v2.0.0.html, sections 2 and 9-11.
Kept byte-identical in Core, SDK and deploy, with a shared conformance corpus.
Build metadata distinguishes immutable release identities, never precedence.
"""

from __future__ import annotations

import re

_NUMBER = r"(?:0|[1-9][0-9]*)"
_VERSION = re.compile(
    rf"(?P<major>{_NUMBER})\.(?P<minor>{_NUMBER})(?:\.(?P<patch>{_NUMBER}))?"
    r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)
ReleaseOrder = tuple[int, int, int, int, tuple[tuple[int, int | str], ...]]


def _parse(value: str, allow_legacy: bool) -> re.Match[str]:
    if not isinstance(value, str):
        raise ValueError("Extension release version must be a SemVer string")
    match = _VERSION.fullmatch(value)
    if match is None:
        raise ValueError(f"Invalid extension release SemVer: {value!r}")
    if match["patch"] is None and not allow_legacy:
        raise ValueError(
            "Extension releases require a complete major.minor.patch version"
        )
    for identifier in (match["pre"] or "").split("."):
        if identifier.isdigit() and identifier != str(int(identifier)):
            raise ValueError(
                "Numeric SemVer prerelease identifiers cannot have leading zeros"
            )
    return match


def release_identity(value: str, allow_legacy: bool = False) -> str:
    """Validate a release identity; explicitly accepted legacy X.Y becomes X.Y.0."""
    match = _parse(value, allow_legacy)
    identity = f"{match['major']}.{match['minor']}.{match['patch'] or '0'}"
    if match["pre"] is not None:
        identity += f"-{match['pre']}"
    if match["build"] is not None:
        identity += f"+{match['build']}"
    return identity


def release_order(value: str, allow_legacy: bool = False) -> ReleaseOrder:
    """Return SemVer precedence; equal precedence does not imply equal identity."""
    match = _parse(value, allow_legacy)
    prerelease = match["pre"]
    identifiers = (
        ()
        if prerelease is None
        else tuple(
            (0, int(item)) if item.isdigit() else (1, item)
            for item in prerelease.split(".")
        )
    )
    return (
        int(match["major"]),
        int(match["minor"]),
        int(match["patch"] or "0"),
        int(prerelease is None),
        identifiers,
    )
