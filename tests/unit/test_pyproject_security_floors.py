"""Security-floor tests for SDK dependency manifests."""

from __future__ import annotations

import json
import re
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib


REPO_ROOT = Path(__file__).resolve().parents[2]
ROOT_PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
EXTENSIONS_LIB_PYPROJECT_PATH = REPO_ROOT / "kamiwaza_extensions_lib" / "pyproject.toml"
TS_EXTENSIONS_LIB_ROOT = REPO_ROOT / "kamiwaza-ai-extensions-lib"


def _minimum_version(requirement_text: str) -> Version:
    requirement = Requirement(requirement_text)
    lower_bounds = [
        Version(spec.version)
        for spec in requirement.specifier
        if spec.operator in {">=", "=="}
    ]
    assert lower_bounds, f"{requirement.name} must declare a lower bound"
    return max(lower_bounds)


def _find_requirement(dependencies: list[str], package_name: str) -> str:
    for dependency in dependencies:
        requirement = Requirement(dependency)
        normalized_name = requirement.name.replace("-", "_")
        if normalized_name == package_name:
            return dependency
    raise AssertionError(f"{package_name} must be declared")


def _node_spec_floor(specifier: str) -> Version:
    match = re.search(r"\d+(?:\.\d+){1,2}", specifier)
    assert match, f"{specifier} must declare a version floor"
    return Version(match.group(0))


def test_runtime_dependency_security_floors():
    """Runtime dependencies should not resolve below patched advisory floors."""

    pyproject = tomllib.loads(ROOT_PYPROJECT_PATH.read_text())
    dependencies = pyproject["project"]["dependencies"]
    connector_extra = pyproject["project"]["optional-dependencies"]["connector"]

    assert _minimum_version(_find_requirement(dependencies, "fastapi")) >= Version("0.136.3")
    assert _minimum_version(_find_requirement(dependencies, "starlette")) >= Version("1.3.1")
    assert _minimum_version(_find_requirement(connector_extra, "fastapi")) >= Version("0.136.3")
    assert _minimum_version(_find_requirement(connector_extra, "starlette")) >= Version("1.3.1")
    assert _minimum_version(_find_requirement(dependencies, "urllib3")) >= Version("2.7.0")
    assert _minimum_version(_find_requirement(dependencies, "requests")) >= Version("2.33.0")
    assert _minimum_version(_find_requirement(dependencies, "idna")) >= Version("3.15")
    assert _minimum_version(_find_requirement(dependencies, "click")) >= Version("8.3.3")
    assert _minimum_version(_find_requirement(dependencies, "typer")) >= Version("0.24.1")
    assert _minimum_version(_find_requirement(dependencies, "pygments")) >= Version("2.20.0")


def test_dev_and_build_dependency_security_floors():
    """Dev and build tooling should not resolve below patched advisory floors."""

    pyproject = tomllib.loads(ROOT_PYPROJECT_PATH.read_text())
    dev_dependencies = pyproject["dependency-groups"]["dev"]
    build_dependencies = pyproject["build-system"]["requires"]

    assert _minimum_version(_find_requirement(dev_dependencies, "black")) >= Version("26.5.1")
    assert _minimum_version(_find_requirement(dev_dependencies, "cryptography")) >= Version("48.0.1")
    assert _minimum_version(_find_requirement(dev_dependencies, "pytest")) >= Version("9.0.3")
    assert _minimum_version(_find_requirement(build_dependencies, "wheel")) >= Version("0.46.2")


def test_extensions_lib_dependency_security_floors():
    """Workspace member dependencies should keep the same patched floors."""

    pyproject = tomllib.loads(EXTENSIONS_LIB_PYPROJECT_PATH.read_text())
    dependencies = pyproject["project"]["dependencies"]
    build_dependencies = pyproject["build-system"]["requires"]

    assert _minimum_version(_find_requirement(dependencies, "fastapi")) >= Version("0.136.3")
    assert _minimum_version(_find_requirement(dependencies, "starlette")) >= Version("1.3.1")
    assert _minimum_version(_find_requirement(build_dependencies, "wheel")) >= Version("0.46.2")


def test_extensions_lib_javascript_security_floors():
    """The TS library manifest and both committed lockfiles should stay patched."""

    package_json = json.loads((TS_EXTENSIONS_LIB_ROOT / "package.json").read_text())
    overrides = package_json["overrides"]

    assert _node_spec_floor(overrides["esbuild"]) >= Version("0.28.1")
    assert _node_spec_floor(overrides["postcss"]) >= Version("8.5.10")

    package_lock = json.loads((TS_EXTENSIONS_LIB_ROOT / "package-lock.json").read_text())
    locked_packages = package_lock["packages"]
    assert Version(locked_packages["node_modules/esbuild"]["version"]) >= Version("0.28.1")
    assert Version(locked_packages["node_modules/postcss"]["version"]) >= Version("8.5.10")

    bun_lock = (TS_EXTENSIONS_LIB_ROOT / "bun.lock").read_text()
    assert '"esbuild": "^0.28.1"' in bun_lock
    assert '"postcss": "^8.5.10"' in bun_lock
    assert "esbuild@0.27.5" not in bun_lock
    assert "postcss@8.4.31" not in bun_lock
