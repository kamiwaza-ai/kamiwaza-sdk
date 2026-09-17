"""Keep fixture artifact identity and retained-package handling coordinated."""

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from kamiwaza_sdk.validation import federation_fixture, federation_setup
from kamiwaza_sdk.validation.provider import ProviderContractError
from tests.integration import _gate_fixture, _mini_access_tier

pytestmark = pytest.mark.unit


def _client(items: list) -> SimpleNamespace:
    packages = Mock()
    packages.list.return_value = SimpleNamespace(items=items)
    packages.install.return_value = SimpleNamespace(
        package=SimpleNamespace(classpaths=[federation_fixture.GATE_CLASSPATH])
    )
    gates = SimpleNamespace(packages=packages, discover=Mock())
    gates.discover.return_value = SimpleNamespace(name=federation_fixture.GATE_NAME)
    return SimpleNamespace(gates=gates)


def test_fresh_fixture_uses_new_artifact_identity(tmp_path: Path) -> None:
    fixture_project = (
        Path(__file__).parents[1] / "integration/fixtures/acme-gates/pyproject.toml"
    )
    version = tomllib.loads(fixture_project.read_text())["project"]["version"]
    assert federation_fixture.GATE_PACKAGE_SPEC == f"acme-gates=={version}"
    assert version == "1.2.0"
    assert _mini_access_tier.WHEEL_NAME == _gate_fixture.WHEEL_NAME
    wheel = tmp_path / _gate_fixture.WHEEL_NAME
    wheel.write_bytes(b"new-fixture-wheel")
    client = _client([])

    _mini_access_tier.install_gate_package(client, str(tmp_path), "file:///fixture")

    client.gates.packages.install.assert_called_once_with(
        "acme-gates==1.2.0",
        hash_digest="sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest(),
        index_url="file:///fixture",
    )
    client.gates.discover.assert_called_once_with(federation_fixture.GATE_CLASSPATH)


def test_compatible_interrupted_fixture_is_reused(tmp_path: Path) -> None:
    installed = SimpleNamespace(
        name="acme-gates",
        version="1.2.0",
        classpaths=[federation_fixture.GATE_CLASSPATH],
    )
    client = _client([installed])
    _mini_access_tier.install_gate_package(client, str(tmp_path), "file:///fixture")
    assert federation_setup._ensure_gate(client) is False
    client.gates.packages.install.assert_not_called()
    client.gates.packages.replace.assert_not_called()
    client.gates.packages.uninstall.assert_not_called()


def test_validation_fresh_fixture_installs_new_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAMIWAZA_FEDERATION_GATE_HASH", "sha256:new-wheel")
    monkeypatch.setenv("KAMIWAZA_FEDERATION_GATE_INDEX_URL", "file:///fixture")
    monkeypatch.delenv("KAMIWAZA_FEDERATION_GATE_PACKAGE_SPEC", raising=False)
    client = _client([])
    assert federation_setup._ensure_gate(client) is True
    client.gates.packages.install.assert_called_once_with(
        "acme-gates==1.2.0", hash_digest="sha256:new-wheel", index_url="file:///fixture"
    )


@pytest.mark.parametrize("entrypoint", ["integration", "validation"])
def test_retained_incompatible_fixture_rejected_without_mutation(
    tmp_path: Path,
    entrypoint: str,
) -> None:
    client = _client(
        [SimpleNamespace(name="acme-gates", version="1.1.0", classpaths=[])]
    )
    with pytest.raises(
        (RuntimeError, ProviderContractError), match="Incompatible retained fixture"
    ) as exc:
        if entrypoint == "integration":
            _mini_access_tier.install_gate_package(
                client, str(tmp_path), "file:///fixture"
            )
        else:
            federation_setup._ensure_gate(client)
    assert "isolated test cluster" in str(exc.value)
    assert "acme-gates==1.2.0" in str(exc.value)
    client.gates.packages.install.assert_not_called()
    client.gates.packages.replace.assert_not_called()
    client.gates.packages.uninstall.assert_not_called()
    client.gates.discover.assert_not_called()


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
