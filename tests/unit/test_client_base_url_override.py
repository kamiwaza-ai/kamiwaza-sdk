from __future__ import annotations

import pytest

from kamiwaza_sdk.client import KamiwazaClient

pytestmark = pytest.mark.unit


class _StubResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    text = "{}"

    def json(self) -> object:
        return {"ok": True}


def _client_capturing_url(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[KamiwazaClient, list[str]]:
    client = KamiwazaClient(base_url="https://example.test/api")
    seen: list[str] = []

    def fake_request(method, url, **kwargs):
        seen.append(url)
        return _StubResponse()

    monkeypatch.setattr(client.session, "request", fake_request)
    return client, seen


def test_request_uses_platform_base_url_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, seen = _client_capturing_url(monkeypatch)

    client._request("GET", "models/")

    assert seen == ["https://example.test/api/models/"]


def test_request_base_url_override_same_host_extension_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, seen = _client_capturing_url(monkeypatch)

    # In-cluster extensions (e.g. Kaizen) sit on the platform host at a
    # different path; the override is allowed.
    client._request(
        "POST", "api/agents/", base_url="https://example.test/runtime/apps/kaizen-1"
    )

    assert seen == ["https://example.test/runtime/apps/kaizen-1/api/agents/"]


def test_request_base_url_override_rejects_foreign_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, seen = _client_capturing_url(monkeypatch)

    # A cross-host base_url must not receive the platform bearer token.
    with pytest.raises(ValueError, match="not on the platform host"):
        client._request("POST", "api/agents/", base_url="https://evil.example/kaizen")

    assert seen == []
