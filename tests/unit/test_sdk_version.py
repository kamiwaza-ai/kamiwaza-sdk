"""Release metadata guards for the SDK distribution."""

from __future__ import annotations

from pathlib import Path

import pytest
from packaging.version import Version

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]


def _load_toml(name: str) -> dict:
    with (_ROOT / name).open("rb") as handle:
        return tomllib.load(handle)


def test_flight_release_has_versioned_sdk_minimum():
    version = _load_toml("pyproject.toml")["project"]["version"]

    assert Version(version) >= Version("1.1.0")


def test_sdk_lock_version_matches_project_metadata():
    project_version = _load_toml("pyproject.toml")["project"]["version"]
    packages = _load_toml("uv.lock")["package"]
    locked_sdk = next(package for package in packages if package["name"] == "kamiwaza-sdk")

    assert locked_sdk["version"] == project_version


def test_sdk_dependency_enforces_cryptography_floor():
    dependencies = _load_toml("pyproject.toml")["project"]["dependencies"]

    assert "cryptography>=50.0.1,<51.0.0" in dependencies
