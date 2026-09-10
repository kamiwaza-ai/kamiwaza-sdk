from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.unit

CONFTEST_PATH = (
    Path(__file__).resolve().parents[1] / "integration" / "conftest.py"
)


@pytest.fixture
def integration_conftest():
    spec = importlib.util.spec_from_file_location(
        "_integration_conftest_runtime_host_under_test", CONFTEST_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _completed(stdout: str = "", returncode: int = 0) -> Any:
    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


def test_runtime_host_explicit_override_wins(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAMIWAZA_RUNTIME_HOST", "192.0.2.10")
    monkeypatch.setattr(integration_conftest.sys, "platform", "linux")
    monkeypatch.setattr(
        integration_conftest.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("explicit runtime host should not probe"),
    )

    assert (
        integration_conftest._runtime_endpoint("http://localhost:19100/data")
        == "http://192.0.2.10:19100/data"
    )


def test_runtime_host_uses_linux_kube_bridge(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KAMIWAZA_RUNTIME_HOST", raising=False)
    monkeypatch.setattr(integration_conftest.sys, "platform", "linux")

    def fake_run(args: list[str], **_kwargs: Any) -> Any:
        if args[-1] == "kube-bridge":
            return _completed("667: kube-bridge inet 10.244.0.1/24 brd 10.244.0.255\n")
        return _completed(returncode=1)

    monkeypatch.setattr(integration_conftest.subprocess, "run", fake_run)

    assert integration_conftest._runtime_host("localhost") == "10.244.0.1"
    assert (
        integration_conftest._runtime_endpoint("http://127.0.0.1:41687/bucket")
        == "http://10.244.0.1:41687/bucket"
    )


def test_runtime_host_falls_back_when_ip_binary_is_missing(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KAMIWAZA_RUNTIME_HOST", raising=False)
    monkeypatch.setattr(integration_conftest.sys, "platform", "linux")
    monkeypatch.setattr(integration_conftest, "RUNTIME_HOST_ALIAS", "host.docker.internal")

    def fake_run(*_args: Any, **_kwargs: Any) -> Any:
        raise FileNotFoundError("ip")

    monkeypatch.setattr(integration_conftest.subprocess, "run", fake_run)

    assert integration_conftest._runtime_host("localhost") == "host.docker.internal"


def test_runtime_endpoint_leaves_remote_hosts_alone(
    integration_conftest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAMIWAZA_RUNTIME_HOST", "192.0.2.10")

    assert (
        integration_conftest._runtime_endpoint("http://minio.example.test:9000/bucket")
        == "http://minio.example.test:9000/bucket"
    )
