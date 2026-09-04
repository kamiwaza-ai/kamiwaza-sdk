"""Offline contracts for the gate-package NetworkPolicy probes."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.integration.gate_packages import test_lifecycle as lifecycle

pytestmark = pytest.mark.unit
_LIVE_WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github/workflows/extension-contract-live.yml"
)


def test_gate_package_client_uses_canonical_live_session_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate-package tests must not require a second raw-token auth channel."""
    monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "true")
    session_client = object()

    class Request:
        def getfixturevalue(self, name: str) -> object:
            assert name == "live_kamiwaza_session_client"
            return session_client

    assert lifecycle.kz.__wrapped__(Request()) is session_client


def test_gate_package_client_fails_when_canonical_auth_would_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A qualified gate-package lane must fail closed on unusable auth."""
    monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "true")

    class Request:
        def getfixturevalue(self, name: str) -> object:
            assert name == "live_kamiwaza_session_client"
            pytest.skip("Unable to build authenticated live client")

    with pytest.raises(
        pytest.fail.Exception, match="requires authenticated live access"
    ):
        lifecycle.kz.__wrapped__(Request())


def test_gate_package_client_requires_explicit_tls_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Qualified callers must explicitly choose strict TLS or a dev opt-out."""
    monkeypatch.delenv("KAMIWAZA_VERIFY_SSL", raising=False)

    class Request:
        def getfixturevalue(self, name: str) -> object:
            pytest.fail(f"unexpected fixture lookup: {name}")

    with pytest.raises(pytest.fail.Exception, match="explicit KAMIWAZA_VERIFY_SSL"):
        lifecycle.kz.__wrapped__(Request())


def test_live_workflow_uses_canonical_api_key_channel() -> None:
    """The qualified live lane must authenticate the canonical fixture."""
    workflow = _LIVE_WORKFLOW.read_text(encoding="utf-8")

    assert "KAMIWAZA_API_KEY: ${{ secrets.KAMIWAZA_API_KEY }}" in workflow
    assert 'KAMIWAZA_VERIFY_SSL: "true"' in workflow
    assert "KAMIWAZA_ADMIN_TOKEN:" not in workflow


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
