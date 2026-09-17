"""Keep fixture artifact identity and retained-package handling coordinated."""

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from zipfile import ZipFile, ZipInfo

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from kamiwaza_sdk.validation import federation_fixture, federation_setup
from kamiwaza_sdk.validation.provider import ProviderContractError
from tests.integration import _gate_fixture, _mini_access_tier

pytestmark = pytest.mark.unit


def _client(items: list, package: SimpleNamespace) -> SimpleNamespace:
    packages = Mock()
    packages.list.return_value = SimpleNamespace(items=items)
    packages.install.return_value = SimpleNamespace(package=package)
    gates = SimpleNamespace(packages=packages, discover=Mock())
    gates.discover.return_value = SimpleNamespace(name=federation_fixture.GATE_NAME)
    return SimpleNamespace(gates=gates)


def _write_fixture_wheel(path: Path, source: str) -> None:
    files = {
        "acme_gates/__init__.py": "",
        "acme_gates/mini_access_tier_gate.py": source,
        "acme_gates-1.2.0.dist-info/METADATA": (
            "Metadata-Version: 2.1\nName: acme-gates\nVersion: 1.2.0\n"
        ),
        "acme_gates-1.2.0.dist-info/WHEEL": (
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    record = "".join(f"{name},,\n" for name in files)
    files["acme_gates-1.2.0.dist-info/RECORD"] = (
        record + "acme_gates-1.2.0.dist-info/RECORD,,\n"
    )
    with ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(ZipInfo(name), content)


def _fixture_source() -> str:
    return (
        Path(__file__).parents[1]
        / "integration/fixtures/acme-gates/acme_gates/mini_access_tier_gate.py"
    ).read_text()


@pytest.fixture
def gate_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    wheel = tmp_path / _gate_fixture.WHEEL_NAME
    _write_fixture_wheel(wheel, _fixture_source())
    digest = _mini_access_tier._wheel_sha256(str(tmp_path))
    monkeypatch.setenv("KAMIWAZA_FEDERATION_GATE_HASH", digest)
    monkeypatch.setenv("KAMIWAZA_FEDERATION_GATE_INDEX_URL", "file:///fixture")
    monkeypatch.delenv("KAMIWAZA_FEDERATION_GATE_PACKAGE_SPEC", raising=False)
    package = SimpleNamespace(
        name="acme-gates",
        package_spec=federation_fixture.GATE_PACKAGE_SPEC,
        version="1.2.0",
        hash_digest=digest,
        status="active",
        classpaths=[federation_fixture.GATE_CLASSPATH],
    )
    return tmp_path, package


def _ensure(entrypoint, client, directory):
    if entrypoint == "integration":
        return _mini_access_tier.install_gate_package(
            client, str(directory), "file:///fixture"
        )
    return federation_setup._ensure_gate(client)


def _assert_unmodified(client):
    client.gates.packages.install.assert_not_called()
    client.gates.packages.replace.assert_not_called()
    client.gates.packages.uninstall.assert_not_called()
    client.gates.discover.assert_not_called()


def test_fixture_build_version_matches_exact_spec() -> None:
    fixture_project = (
        Path(__file__).parents[1] / "integration/fixtures/acme-gates/pyproject.toml"
    )
    version = tomllib.loads(fixture_project.read_text())["project"]["version"]
    assert federation_fixture.GATE_PACKAGE_SPEC == f"acme-gates=={version}"
    assert version == "1.2.0"
    assert _mini_access_tier.WHEEL_NAME == _gate_fixture.WHEEL_NAME


@pytest.mark.parametrize("entrypoint", ["integration", "validation"])
def test_fresh_fixture_uses_exact_artifact_identity(gate_fixture, entrypoint):
    directory, package = gate_fixture
    client = _client([], package)
    _ensure(entrypoint, client, directory)
    client.gates.packages.install.assert_called_once_with(
        package.package_spec,
        hash_digest=package.hash_digest,
        index_url="file:///fixture",
    )
    client.gates.discover.assert_called_once_with(federation_fixture.GATE_CLASSPATH)
    client.gates.packages.replace.assert_not_called()
    client.gates.packages.uninstall.assert_not_called()


@pytest.mark.parametrize("entrypoint", ["integration", "validation"])
def test_compatible_interrupted_fixture_is_reused(gate_fixture, entrypoint):
    directory, package = gate_fixture
    client = _client([package], package)
    _ensure(entrypoint, client, directory)
    client.gates.packages.install.assert_not_called()
    client.gates.packages.replace.assert_not_called()
    client.gates.packages.uninstall.assert_not_called()
    client.gates.discover.assert_called_once_with(federation_fixture.GATE_CLASSPATH)


@pytest.mark.parametrize("entrypoint", ["integration", "validation"])
@pytest.mark.parametrize(
    "mismatch",
    [
        {"version": "1.1.0"},
        {"package_spec": "acme-gates==1.1.0"},
        {"hash_digest": "sha256:" + "1" * 64},
        {"status": "inactive"},
        {"classpaths": []},
        {"version": None},
        {"package_spec": None},
        {"hash_digest": None},
        {"status": None},
    ],
)
def test_retained_mismatched_or_missing_identity_is_rejected(
    gate_fixture, entrypoint, mismatch
):
    directory, package = gate_fixture
    package.__dict__.update(mismatch)
    client = _client([package], package)
    with pytest.raises((RuntimeError, ProviderContractError), match="Incompatible"):
        _ensure(entrypoint, client, directory)
    _assert_unmodified(client)


@pytest.mark.parametrize("entrypoint", ["integration", "validation"])
def test_same_name_version_classpath_other_artifact_is_not_reused(
    gate_fixture, entrypoint
):
    directory, package = gate_fixture
    # Distinct, neutral core-like gate semantics must not pass by name alone.
    other_dir = directory / "other"
    other_dir.mkdir()
    other = other_dir / _gate_fixture.WHEEL_NAME
    source = _fixture_source()
    other_source = source.replace('"PUBLIC"', '"BASIC"')
    other_source = other_source.replace('"PRIVATE"', '"TEAM"')
    other_source = other_source.replace('"CONFIDENTIAL"', '"PREMIUM"')
    other_source = other_source.replace('"tier"', '"record_tier"')
    other_source = other_source.replace("tier_field", "record_tier_field")
    other_source = other_source.replace("x-user-access-tier", "x-user-access_tier")
    other_source = other_source.replace("required=True", "required=False")
    assert source != other_source
    _write_fixture_wheel(other, other_source)
    with ZipFile(other) as archive:
        assert (
            "Version: 1.2.0"
            in archive.read("acme_gates-1.2.0.dist-info/METADATA").decode()
        )
        assert (
            "class MiniAccessTierGate"
            in archive.read("acme_gates/mini_access_tier_gate.py").decode()
        )
    package.hash_digest = "sha256:" + hashlib.sha256(other.read_bytes()).hexdigest()
    client = _client([package], package)
    with pytest.raises((RuntimeError, ProviderContractError), match="hash_digest"):
        _ensure(entrypoint, client, directory)
    _assert_unmodified(client)


@pytest.mark.parametrize("entrypoint", ["integration", "validation"])
@pytest.mark.parametrize("field", ["version", "hash_digest", "status", "package_spec"])
def test_fresh_install_metadata_is_validated_before_discovery(
    gate_fixture, entrypoint, field
):
    directory, package = gate_fixture
    setattr(package, field, None)
    client = _client([], package)
    with pytest.raises((RuntimeError, ProviderContractError), match="Incompatible"):
        _ensure(entrypoint, client, directory)
    client.gates.packages.install.assert_called_once()
    client.gates.discover.assert_not_called()
    client.gates.packages.replace.assert_not_called()
    client.gates.packages.uninstall.assert_not_called()


@pytest.mark.parametrize("entrypoint", ["integration", "validation"])
def test_unreadable_package_listing_never_installs(gate_fixture, entrypoint):
    directory, package = gate_fixture
    client = _client([], package)
    client.gates.packages.list.side_effect = RuntimeError("read unavailable")
    with pytest.raises(RuntimeError, match="read unavailable"):
        _ensure(entrypoint, client, directory)
    _assert_unmodified(client)


@pytest.mark.parametrize("entrypoint", ["integration", "validation"])
def test_reuse_requires_expected_artifact_identity(
    gate_fixture, entrypoint, monkeypatch
):
    directory, package = gate_fixture
    client = _client([package], package)
    if entrypoint == "integration":
        (directory / _gate_fixture.WHEEL_NAME).unlink()
    else:
        monkeypatch.delenv("KAMIWAZA_FEDERATION_GATE_HASH")
    with pytest.raises((FileNotFoundError, ProviderContractError)):
        _ensure(entrypoint, client, directory)
    _assert_unmodified(client)


def test_old_wheel_does_not_satisfy_new_fixture_prerequisite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("M5_TEST_WHEEL_DIR", str(tmp_path))
    monkeypatch.setenv("M5_TEST_INDEX_URL", "file:///fixture")
    (tmp_path / "acme_gates-1.1.0-py3-none-any.whl").touch()
    assert _mini_access_tier.wheel_and_index() is None
    (tmp_path / _gate_fixture.WHEEL_NAME).touch()
    assert _mini_access_tier.wheel_and_index() == (str(tmp_path), "file:///fixture")


@pytest.mark.parametrize("value", ["", "sha256:short"])
def test_validation_rejects_unusable_expected_hash_before_mutation(
    gate_fixture, monkeypatch, value
):
    directory, package = gate_fixture
    client = _client([package], package)
    monkeypatch.setenv("KAMIWAZA_FEDERATION_GATE_HASH", value)
    with pytest.raises(ProviderContractError, match="expected sha256 hash"):
        _ensure("validation", client, directory)
    _assert_unmodified(client)


def test_validation_reuse_honors_configured_exact_spec(gate_fixture, monkeypatch):
    directory, package = gate_fixture
    package.package_spec = "acme-gates==1.2.7"
    package.version = "1.2.7"
    monkeypatch.setenv("KAMIWAZA_FEDERATION_GATE_PACKAGE_SPEC", package.package_spec)
    client = _client([package], package)
    assert _ensure("validation", client, directory) is False
    client.gates.packages.install.assert_not_called()
    client.gates.packages.replace.assert_not_called()
    client.gates.packages.uninstall.assert_not_called()
