"""Security-floor tests for SDK dependency manifests."""

from __future__ import annotations

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


def test_runtime_dependency_security_floors():
    """Runtime dependencies should not resolve below patched advisory floors."""

    pyproject = tomllib.loads(ROOT_PYPROJECT_PATH.read_text())
    dependencies = pyproject["project"]["dependencies"]
    connector_extra = pyproject["project"]["optional-dependencies"]["connector"]

    assert _minimum_version(_find_requirement(dependencies, "fastapi")) >= Version("0.136.3")
    assert _minimum_version(_find_requirement(connector_extra, "fastapi")) >= Version("0.136.3")
    assert _minimum_version(_find_requirement(dependencies, "urllib3")) >= Version("2.7.0")


def test_dev_and_build_dependency_security_floors():
    """Dev and build tooling should not resolve below patched advisory floors."""

    pyproject = tomllib.loads(ROOT_PYPROJECT_PATH.read_text())
    dev_dependencies = pyproject["dependency-groups"]["dev"]
    build_dependencies = pyproject["build-system"]["requires"]

    assert _minimum_version(_find_requirement(dev_dependencies, "black")) >= Version("26.5.1")
    assert _minimum_version(_find_requirement(dev_dependencies, "cryptography")) >= Version("48.0.1")
    assert _minimum_version(_find_requirement(build_dependencies, "wheel")) >= Version("0.46.2")


def test_extensions_lib_dependency_security_floors():
    """Workspace member dependencies should keep the same patched floors."""

    pyproject = tomllib.loads(EXTENSIONS_LIB_PYPROJECT_PATH.read_text())
    dependencies = pyproject["project"]["dependencies"]
    build_dependencies = pyproject["build-system"]["requires"]

    assert _minimum_version(_find_requirement(dependencies, "fastapi")) >= Version("0.136.3")
    assert _minimum_version(_find_requirement(build_dependencies, "wheel")) >= Version("0.46.2")
