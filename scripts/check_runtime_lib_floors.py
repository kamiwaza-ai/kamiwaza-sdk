#!/usr/bin/env python3
"""Fail when a declared runtime-lib floor exceeds the newest published version.

The SDK declares a floor for ``kamiwaza-extensions-lib`` in three places, and
every one of them is resolved by a *consumer* against a public registry:

* ``pyproject.toml`` ``[project].dependencies`` — what ``pip install kamiwaza-sdk``
  resolves against PyPI.
* ``kamiwaza_extensions/compatibility.json`` ``runtime_lib_compat.python`` — what
  the scaffolder renders into a generated extension's ``requirements.txt``.
* ``runtime_lib_compat.typescript`` — the npm counterpart rendered into a
  generated extension's ``package.json``.

Bumping the in-repo version of a runtime lib is not the same as publishing it.
When a floor moves ahead of the registry, nothing breaks for anyone developing
with ``uv`` (the workspace source shadows the registry), so the gap is invisible
locally and only surfaces as an unresolvable install for external consumers and
for CI lanes that install with plain ``pip``/``npm``.

The in-repo *version* of a lib is deliberately allowed to run ahead of the
registry — that is the normal state between a bump and its release. Only the
declared *floors* are checked, because those are what consumers must resolve.

Two modes, because a floor ahead of the registry means different things in
different places. On a release branch it means a broken release is about to
ship, so it is an error. On the integration branch it is the ordinary staging
state between bumping a runtime lib and the release that publishes it, so it is
reported as a warning and left for the publish to clear.

Exit codes: ``0`` all floors resolvable (or ``--warn-only``), ``1`` at least one
floor exceeds the newest published version.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:  # tomllib landed in 3.11; the repo still supports 3.10
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib  # type: ignore[no-redef,import-not-found]

from packaging.version import InvalidVersion, Version

PYPI_LIB = "kamiwaza-extensions-lib"
NPM_LIB = "@kamiwaza-ai/extensions-lib"
NETWORK_TIMEOUT = 15

# Matches the lower bound of both a PEP 440 specifier set
# (``>=0.4.4,<0.5``) and an npm range (``>=0.4.3 <0.5``).
_FLOOR_RE = re.compile(r">=\s*v?(\d+(?:\.\d+)*(?:[.\-+][0-9A-Za-z.\-+]+)?)")


@dataclass(frozen=True)
class Declaration:
    """A floor a consumer will resolve against a public registry."""

    ecosystem: str  # "pypi" | "npm"
    package: str
    floor: str
    source: str  # human-readable "file → field", for the error message


@dataclass(frozen=True)
class Violation:
    declaration: Declaration
    published: str


def extract_floor(spec: str) -> str | None:
    """Return the ``>=`` lower bound of a version range, or None if unbounded.

    A range with no lower bound cannot outrun the registry, so it is not a
    finding — the caller skips it rather than treating it as an error.
    """
    match = _FLOOR_RE.search(spec)
    return match.group(1) if match else None


def _declaration(
    ecosystem: str, package: str, spec: str, source: str
) -> Declaration | None:
    floor = extract_floor(spec)
    return Declaration(ecosystem, package, floor, source) if floor else None


def _sdk_dependency_spec(repo_root: Path) -> str | None:
    """The ``kamiwaza-extensions-lib`` requirement from the root pyproject."""
    with (repo_root / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)
    for raw in pyproject.get("project", {}).get("dependencies", []):
        # Split off the name without pulling in a full requirement parser:
        # the name ends at the first specifier/marker/extra character.
        name = re.split(r"[<>=!~;\[ ]", raw, maxsplit=1)[0].strip()
        if name == PYPI_LIB:
            return raw
    return None


def _bundle_specs(repo_root: Path) -> dict[str, str]:
    """The python + typescript pins from the compatibility bundle."""
    bundle_path = repo_root / "kamiwaza_extensions" / "compatibility.json"
    compat = json.loads(bundle_path.read_text()).get("runtime_lib_compat", {})
    return {
        "python": compat.get("python", {}).get(PYPI_LIB, ""),
        "typescript": compat.get("typescript", {}).get(NPM_LIB, ""),
    }


def collect_declarations(repo_root: Path) -> list[Declaration]:
    """Every floor in the repo that a consumer resolves against a registry."""
    candidates: list[Declaration | None] = []

    dependency_spec = _sdk_dependency_spec(repo_root)
    if dependency_spec:
        candidates.append(
            _declaration(
                "pypi",
                PYPI_LIB,
                dependency_spec,
                "pyproject.toml → [project].dependencies",
            )
        )

    specs = _bundle_specs(repo_root)
    candidates.append(
        _declaration(
            "pypi",
            PYPI_LIB,
            specs["python"],
            "compatibility.json → runtime_lib_compat.python",
        )
    )
    candidates.append(
        _declaration(
            "npm",
            NPM_LIB,
            specs["typescript"],
            "compatibility.json → runtime_lib_compat.typescript",
        )
    )
    return [c for c in candidates if c is not None]


def _fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=NETWORK_TIMEOUT) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def latest_published(ecosystem: str, package: str) -> str:
    """Newest version of ``package`` on its public registry."""
    if ecosystem == "pypi":
        return _fetch_json(f"https://pypi.org/pypi/{package}/json")["info"]["version"]
    return _fetch_json(f"https://registry.npmjs.org/{package}")["dist-tags"]["latest"]


def find_violations(
    declarations: list[Declaration],
    resolver: Callable[[str, str], str],
) -> list[Violation]:
    """Declarations whose floor is newer than what the registry actually serves.

    ``resolver`` is injected so the comparison is testable without network.
    """
    violations = []
    for declaration in declarations:
        published = resolver(declaration.ecosystem, declaration.package)
        if _is_ahead(declaration.floor, published):
            violations.append(Violation(declaration, published))
    return violations


def _is_ahead(floor: str, published: str) -> bool:
    try:
        return Version(floor) > Version(published)
    except InvalidVersion:
        # An unparseable version is a manifest problem, not a publish gap;
        # report it as clean here so the message stays accurate.
        return False


def _report(violations: list[Violation], warn_only: bool) -> None:
    level = "warning" if warn_only else "error"
    remedy = (
        "Publish it, or lower the floor to a released version."
        if warn_only
        else "Publish it before merging, or lower the floor to a released version."
    )
    for violation in violations:
        declaration = violation.declaration
        print(
            f"::{level}::{declaration.package} floor {declaration.floor} "
            f"({declaration.source}) exceeds the newest published version "
            f"{violation.published}. {remedy}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="Report violations as warnings and exit 0 (integration branches).",
    )
    args = parser.parse_args(argv)

    declarations = collect_declarations(args.repo_root)
    try:
        violations = find_violations(declarations, latest_published)
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
        # A registry outage must not turn the whole repo red. The npm-side
        # version guard makes the same call, and release.sh's own preflight
        # still hard-fails at publish time.
        print(
            f"::warning::Could not reach a package registry ({exc}); skipping floor check."
        )
        return 0

    _report(violations, args.warn_only)
    if violations:
        return 0 if args.warn_only else 1

    for declaration in declarations:
        print(
            f"OK: {declaration.package} floor {declaration.floor} ({declaration.source})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
