#!/usr/bin/env python3
"""Fail when a declared runtime-lib floor exceeds the newest published version.

The SDK declares a floor for ``kamiwaza-extensions-lib`` in several places, and
every one of them is resolved by a *consumer* against a public registry:

* ``pyproject.toml`` ``[project].dependencies`` — what ``pip install kamiwaza-sdk``
  resolves against PyPI.
* ``kamiwaza_extensions/compatibility.json`` — what the scaffolder renders into a
  generated extension's ``requirements.txt`` / ``package.json``, for both
  ecosystems.
* the bundled example app's manifests — code users copy verbatim.

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

Failing open is the one outcome this must never have: a guard that reports green
when it could not actually check is worse than no guard. So every declaration
prints a line, an unresolvable package name is a finding rather than an outage,
and a spec whose lower bound cannot be determined says so out loud.

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
from typing import Callable, Iterable, Literal

try:  # tomllib landed in 3.11; the repo still supports 3.10
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib  # type: ignore[no-redef,import-not-found]

from packaging.version import InvalidVersion, Version

Ecosystem = Literal["pypi", "npm"]

PYPI_LIB = "kamiwaza-extensions-lib"
NPM_LIB = "@kamiwaza-ai/extensions-lib"
NETWORK_TIMEOUT = 15

# One clause of a version range, across PEP 440 and npm syntax. Scanning clauses
# rather than searching for a lower bound directly is what keeps `<0.5` from
# being read as a floor of 0.5.
_CLAUSE_RE = re.compile(
    r"(?P<op><=|>=|==|!=|~=|<|>|\^|~)?\s*v?"
    r"(?P<version>\d+(?:\.\d+)*(?:[-+.][0-9A-Za-z.\-+]*)?)"
)

# Operators that impose a lower bound. The empty string covers a bare pin
# (`0.4.4`); `^` and `~` are npm's, and are lower bounds despite also capping.
_LOWER_BOUND_OPS = frozenset({">=", ">", "==", "~=", "^", "~", ""})


class RegistryError(Exception):
    """A registry lookup did not yield a usable answer.

    ``hard`` distinguishes "the registry told us something is wrong" (a 404 for
    a package that should exist, no publishable versions) from "we could not
    reach the registry". The first is a finding; the second must not fail the
    build, or every network blip turns the repo red.
    """

    def __init__(self, message: str, hard: bool) -> None:
        super().__init__(message)
        self.hard = hard


@dataclass(frozen=True)
class Floor:
    """The lower bound a consumer must be able to resolve."""

    version: str
    strict: bool  # `>x` rather than `>=x`

    def __str__(self) -> str:
        return f"{'>' if self.strict else '>='}{self.version}"


@dataclass(frozen=True)
class Declaration:
    ecosystem: Ecosystem
    package: str
    spec: str
    floor: Floor | None  # None when the spec imposes no lower bound
    source: str  # human-readable "file → field", for the message


@dataclass(frozen=True)
class Finding:
    declaration: Declaration
    detail: str


def extract_floor(spec: str) -> Floor | None:
    """Return the lower bound of a version range, or None if it has none.

    Handles both ecosystems' syntax: ``>=0.4.4,<0.5``, ``>=0.4.3 <0.5``,
    ``^0.4.3``, ``~0.4.3``, ``==0.4.4``, ``~=0.4.4``, ``>0.4.4``, and a bare pin.
    A genuinely unbounded range (``<0.5``, ``*``) cannot outrun the registry and
    correctly yields None.
    """
    best: Floor | None = None
    for match in _CLAUSE_RE.finditer(spec):
        operator = match.group("op") or ""
        if operator not in _LOWER_BOUND_OPS:
            continue
        candidate = Floor(match.group("version"), strict=operator == ">")
        if best is None or _version_gt(candidate.version, best.version):
            best = candidate
    return best


def _version_gt(left: str, right: str) -> bool:
    try:
        return Version(left) > Version(right)
    except InvalidVersion:
        return False


def _declaration(
    ecosystem: Ecosystem, package: str, spec: str, source: str
) -> Declaration:
    return Declaration(ecosystem, package, spec, extract_floor(spec), source)


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


def _example_requirements_specs(
    example: Path,
) -> list[tuple[Ecosystem, str, str, str]]:
    requirements = example / "backend" / "requirements.txt"
    if not requirements.is_file():
        return []
    source = "examples/chatbot-app/backend/requirements.txt"
    return [
        ("pypi", PYPI_LIB, line.strip(), source)
        for line in requirements.read_text().splitlines()
        if line.strip().startswith(PYPI_LIB)
    ]


def _example_package_specs(example: Path) -> list[tuple[Ecosystem, str, str, str]]:
    package_json = example / "frontend" / "package.json"
    if not package_json.is_file():
        return []
    deps = json.loads(package_json.read_text()).get("dependencies", {})
    if NPM_LIB not in deps:
        return []
    source = "examples/chatbot-app/frontend/package.json"
    return [("npm", NPM_LIB, deps[NPM_LIB], source)]


def _example_specs(repo_root: Path) -> list[tuple[Ecosystem, str, str, str]]:
    """Floors in the bundled example app — manifests users copy verbatim.

    Absent files are skipped rather than raising: the examples tree is not load
    bearing for the SDK itself and may be restructured.
    """
    example = repo_root / "examples" / "chatbot-app"
    return [*_example_requirements_specs(example), *_example_package_specs(example)]


def collect_declarations(repo_root: Path) -> list[Declaration]:
    """Every floor in the repo that a consumer resolves against a registry."""
    specs = _bundle_specs(repo_root)
    sources: list[tuple[Ecosystem, str, str, str]] = [
        (
            "pypi",
            PYPI_LIB,
            specs["python"],
            "compatibility.json → runtime_lib_compat.python",
        ),
        (
            "npm",
            NPM_LIB,
            specs["typescript"],
            "compatibility.json → runtime_lib_compat.typescript",
        ),
        *_example_specs(repo_root),
    ]

    dependency_spec = _sdk_dependency_spec(repo_root)
    if dependency_spec:
        sources.insert(
            0,
            (
                "pypi",
                PYPI_LIB,
                dependency_spec,
                "pyproject.toml → [project].dependencies",
            ),
        )

    return [_declaration(*source) for source in sources]


def _fetch_json(url: str) -> dict:
    try:
        with urllib.request.urlopen(
            url, timeout=NETWORK_TIMEOUT
        ) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # HTTPError subclasses URLError, so it has to be caught first or a 404
        # — the package does not exist under that name, which is exactly what
        # this gate is for — reads as an outage and passes silently.
        raise RegistryError(
            f"registry returned HTTP {exc.code}", hard=_is_hard_status(exc.code)
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise RegistryError(f"registry unreachable ({exc})", hard=False) from exc


def _is_hard_status(code: int) -> bool:
    """A 4xx other than rate-limiting is the registry answering, not failing."""
    return 400 <= code < 500 and code != 429


def _max_version(candidates: Iterable[str]) -> str:
    parsed = []
    for raw in candidates:
        try:
            parsed.append((Version(raw), raw))
        except InvalidVersion:
            continue
    if not parsed:
        raise RegistryError("no parseable published versions", hard=True)
    return max(parsed)[1]


def latest_published(ecosystem: str, package: str) -> str:
    """Newest version of ``package`` that a resolver would actually accept.

    Derived from the full release list rather than the registry's own "latest"
    pointer: PyPI's ``info.version`` can name a yanked release that pip refuses,
    and npm's ``dist-tags.latest`` hides versions published under another tag.
    """
    if ecosystem == "pypi":
        data = _fetch_json(f"https://pypi.org/pypi/{package}/json")
        releases = (data.get("releases") or {}).items()
        return _max_version(
            version
            for version, files in releases
            if any(not file.get("yanked", False) for file in files)
        )
    data = _fetch_json(f"https://registry.npmjs.org/{package}")
    return _max_version((data.get("versions") or {}).keys())


def _satisfied(floor: Floor, published: str) -> bool:
    parsed_published = Version(published)
    parsed_floor = Version(floor.version)
    if floor.strict:
        return parsed_published > parsed_floor
    return parsed_published >= parsed_floor


def _evaluate(declaration: Declaration, published: str) -> str | None:
    """Return a problem description, or None when the floor is resolvable."""
    if declaration.floor is None:
        return None
    try:
        if _satisfied(declaration.floor, published):
            return None
    except InvalidVersion:
        # Not a publish gap but not clean either — say so rather than passing.
        return (
            f"could not compare floor {declaration.floor} against published "
            f"{published}: unparseable version"
        )
    return f"floor {declaration.floor} exceeds the newest published version {published}"


def find_violations(
    declarations: list[Declaration],
    resolver: Callable[[str, str], str],
) -> tuple[list[Finding], list[Finding]]:
    """Split declarations into (violations, unchecked).

    ``resolver`` is injected so the comparison is testable without network.
    Each declaration is resolved independently, so one registry being down does
    not skip the other ecosystem's checks.
    """
    violations: list[Finding] = []
    unchecked: list[Finding] = []
    for declaration in declarations:
        try:
            published = resolver(declaration.ecosystem, declaration.package)
        except RegistryError as exc:
            target = violations if exc.hard else unchecked
            target.append(Finding(declaration, str(exc)))
            continue
        problem = _evaluate(declaration, published)
        if problem:
            violations.append(Finding(declaration, problem))
    return violations, unchecked


def _print_findings(findings: list[Finding], level: str, remedy: str) -> None:
    for finding in findings:
        declaration = finding.declaration
        print(
            f"::{level}::{declaration.package} ({declaration.source}): "
            f"{finding.detail}. {remedy}"
        )


def _print_clean(declarations: list[Declaration], reported: set[str]) -> None:
    """Account for every declaration, so a silent pass is impossible."""
    for declaration in declarations:
        if declaration.source in reported:
            continue
        if declaration.floor is None:
            print(
                f"SKIP: {declaration.package} ({declaration.source}) declares "
                f"{declaration.spec!r}, which imposes no lower bound."
            )
        else:
            print(
                f"OK: {declaration.package} {declaration.floor} ({declaration.source})"
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
    if not declarations:
        print("::error::No runtime-lib floors found — this check verified nothing.")
        return 1

    violations, unchecked = find_violations(declarations, latest_published)

    level = "warning" if args.warn_only else "error"
    remedy = "Publish it, or lower the floor to a released version."
    _print_findings(violations, level, remedy)
    _print_findings(unchecked, "warning", "Skipped; the registry was unreachable.")
    _print_clean(
        declarations,
        {f.declaration.source for f in (*violations, *unchecked)},
    )

    if violations:
        return 0 if args.warn_only else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
