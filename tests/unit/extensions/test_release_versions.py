"""Shared SemVer conformance examples, independent of package startup."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "AGENTS.md").is_file()
)
MODULE_PATH = next(
    ROOT / path
    for path in (
        "kamiwaza/serving/garden/apps/release_versions.py",
        "kamiwaza_extensions/release_versions.py",
        "scripts/lib/release_versions.py",
    )
    if (ROOT / path).is_file()
)
SPEC = importlib.util.spec_from_file_location("extension_release_versions", MODULE_PATH)
VERSIONS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERSIONS)
CORPUS = json.loads(
    Path(__file__).with_name("release_versions_corpus.json").read_text()
)


@pytest.mark.parametrize("value", CORPUS["valid"])
def test_valid_identity_is_preserved(value):
    assert VERSIONS.release_identity(value) == value


@pytest.mark.parametrize("value", CORPUS["invalid"])
def test_invalid_publication_version_is_rejected(value):
    with pytest.raises(ValueError):
        VERSIONS.release_identity(value)
    with pytest.raises(ValueError):
        VERSIONS.release_order(value)


def test_semver_precedence():
    ordered = CORPUS["ordered"]
    assert sorted(reversed(ordered), key=VERSIONS.release_order) == ordered
    assert all(
        VERSIONS.release_order(a) < VERSIONS.release_order(b)
        for a, b in zip(ordered, ordered[1:])
    )


@pytest.mark.parametrize("value,expected", CORPUS["legacy"].items())
def test_legacy_abbreviation_requires_explicit_opt_in(value, expected):
    with pytest.raises(ValueError):
        VERSIONS.release_identity(value)
    assert VERSIONS.release_identity(value, allow_legacy=True) == expected
    assert VERSIONS.release_order(value, allow_legacy=True) == VERSIONS.release_order(
        expected
    )


@pytest.mark.parametrize("left,right", CORPUS["equal_precedence"])
def test_build_metadata_changes_identity_not_precedence(left, right):
    assert VERSIONS.release_identity(left) != VERSIONS.release_identity(right)
    assert VERSIONS.release_order(left) == VERSIONS.release_order(right)


@pytest.mark.parametrize("value", ["1", "01.2", "1.02", "1.2-01", "1.2.dev1"])
def test_legacy_opt_in_does_not_enable_non_semver(value):
    with pytest.raises(ValueError):
        VERSIONS.release_identity(value, allow_legacy=True)
