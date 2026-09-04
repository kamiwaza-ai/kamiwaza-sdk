"""Offline contracts for the gate-package NetworkPolicy probes."""

from __future__ import annotations

import subprocess

import pytest

from tests.integration.gate_packages import test_lifecycle as lifecycle

pytestmark = pytest.mark.unit


def test_gate_package_client_uses_canonical_live_session_fixture() -> None:
    """Gate-package tests must not require a second raw-token auth channel."""
    session_client = object()

    assert lifecycle.kz.__wrapped__(session_client) is session_client


def _completed(
    stdout: str = "curl_rc=0 http_code=200\n",
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["kubectl"], 0, stdout, "")


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
def test_required_flag_accepts_truthy_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("M5_TEST_NETWORK_POLICY_REQUIRED", value)
    assert lifecycle._network_policy_required() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
def test_required_flag_defaults_to_optional(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("M5_TEST_NETWORK_POLICY_REQUIRED", value)
    assert lifecycle._network_policy_required() is False


def test_probe_uses_proxy_free_curl_and_parses_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(argv: list[str], args: list[str]):
        observed["argv"] = argv
        observed["args"] = args
        return _completed()

    monkeypatch.setattr(lifecycle, "_kubectl_run", fake_run)
    assert lifecycle._probe(
        ["kubectl"], "worker-0", "ray-worker", "https://mirror.test/simple/"
    ) == (0, 200)
    args = observed["args"]
    assert isinstance(args, list)
    script = args[-1]
    assert "--noproxy '*'" in script
    assert "-u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY" in script
    assert "https://mirror.test/simple/" in script


def test_probe_reports_denied_connection_without_masking_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        lifecycle,
        "_kubectl_run",
        lambda _argv, _args: _completed("curl_rc=28 http_code=000\n"),
    )
    assert lifecycle._probe(
        ["kubectl"], "worker-0", "ray-worker", "https://example.com"
    ) == (28, 0)


def test_running_pod_selector_ignores_pending_rollout_pods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(argv: list[str], args: list[str]):
        observed["args"] = args
        return _completed("worker-1\nworker-2\n")

    monkeypatch.setattr(lifecycle, "_kubectl_run", fake_run)
    assert (
        lifecycle._pod_for_selector(["kubectl"], "ray.io/node-type=worker", "worker")
        == "worker-1"
    )
    args = observed["args"]
    assert isinstance(args, list)
    assert (
        'jsonpath={range .items[?(@.status.phase=="Running")]}{.metadata.name}'
        '{"\\n"}{end}'
    ) in args
