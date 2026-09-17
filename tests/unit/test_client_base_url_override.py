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


def test_request_base_url_override_resolves_relative_extension_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, seen = _client_capturing_url(monkeypatch)

    # The platform reports an in-cluster extension as a path. It is same-origin,
    # so the bearer is safe, and it must be joined onto the platform origin —
    # not the /api prefix — to be issuable.
    client._request("POST", "api/agents", base_url="/runtime/apps/kaizen-ddd84430")

    assert seen == ["https://example.test/runtime/apps/kaizen-ddd84430/api/agents"]


def test_request_base_url_override_rejects_protocol_relative_foreign_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, seen = _client_capturing_url(monkeypatch)

    # //evil.example parses with a netloc, so it is a host reference, not a
    # same-origin path — the guard must still refuse it.
    with pytest.raises(ValueError, match="not on the platform host"):
        client._request("POST", "api/agents", base_url="//evil.example/kaizen")

    assert seen == []


def test_request_base_url_override_rejects_scheme_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, seen = _client_capturing_url(monkeypatch)

    with pytest.raises(ValueError, match="not on the platform host"):
        client._request("POST", "api/agents", base_url="http://example.test/kaizen")

    assert seen == []


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.example\\@example.test/kaizen",
        "https://evil.example\\@example.test",
        "https://evil.example\\.example.test/kaizen",
    ],
)
def test_request_base_url_override_rejects_authority_confusion(
    monkeypatch: pytest.MonkeyPatch, hostile: str
) -> None:
    client, seen = _client_capturing_url(monkeypatch)

    # urllib.parse splits userinfo at the last "@" and reports example.test,
    # while the transport ends the authority at the backslash and connects to
    # evil.example. The guard must agree with the transport, or the platform
    # bearer is delivered off-host.
    with pytest.raises(ValueError, match="not on the platform host"):
        client._request("POST", "api/agents", base_url=hostile)

    assert seen == []


def test_request_base_url_override_allows_legitimate_userinfo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, seen = _client_capturing_url(monkeypatch)

    client._request("POST", "api/agents", base_url="https://u:p@example.test/kaizen")

    assert seen == ["https://u:p@example.test/kaizen/api/agents"]


def test_request_base_url_override_rejects_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, seen = _client_capturing_url(monkeypatch)

    with pytest.raises(ValueError, match="base_url override is empty"):
        client._request("POST", "api/agents", base_url="")

    assert seen == []
