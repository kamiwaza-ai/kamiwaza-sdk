from __future__ import annotations

import pytest

from kamiwaza_sdk.client import KamiwazaClient
from kamiwaza_sdk.exceptions import APIError, VectorDBUnavailableError


pytestmark = pytest.mark.unit


class _StubResponse:
    def __init__(
        self,
        *,
        status_code: int,
        text: str = "",
        content_type: str = "application/json",
        json_data: object | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = {"content-type": content_type}
        self._json_data = json_data

    def json(self) -> object:
        if self._json_data is None:
            raise ValueError("No JSON payload")
        return self._json_data


def _make_client_with_response(
    monkeypatch: pytest.MonkeyPatch,
    response: _StubResponse,
) -> KamiwazaClient:
    client = KamiwazaClient(base_url="https://example.test/api")
    monkeypatch.setattr(client.session, "request", lambda *_args, **_kwargs: response)
    return client


def test_501_vectordb_path_maps_to_vectordb_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _StubResponse(
        status_code=501,
        text='{"detail":"no vector backend configured"}',
        json_data={"detail": "no vector backend configured"},
    )
    client = _make_client_with_response(monkeypatch, response)

    with pytest.raises(VectorDBUnavailableError) as exc_info:
        client.get("/vectordb/collections")

    assert exc_info.value.status_code == 501
    assert exc_info.value.response_data == {"detail": "no vector backend configured"}


def test_501_context_vectordbs_path_maps_to_vectordb_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _StubResponse(
        status_code=501,
        text='{"detail":"no vector backend configured"}',
        json_data={"detail": "no vector backend configured"},
    )
    client = _make_client_with_response(monkeypatch, response)

    with pytest.raises(VectorDBUnavailableError) as exc_info:
        client.get("/context/vectordbs")

    assert exc_info.value.status_code == 501
    assert exc_info.value.response_data == {"detail": "no vector backend configured"}


def test_501_non_vectordb_path_raises_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _StubResponse(
        status_code=501,
        text='{"detail":"feature not implemented"}',
        json_data={"detail": "feature not implemented"},
    )
    client = _make_client_with_response(monkeypatch, response)

    with pytest.raises(APIError) as exc_info:
        client.get("/context/ontologies")

    assert not isinstance(exc_info.value, VectorDBUnavailableError)
    assert exc_info.value.status_code == 501


# ========== absolute_url same-origin guard ==========


def test_absolute_url_rejects_different_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """absolute_url that targets a different host must be rejected to
    prevent credential leakage via SSRF."""
    response = _StubResponse(status_code=200, json_data={})
    client = _make_client_with_response(monkeypatch, response)

    with pytest.raises(ValueError, match="same origin"):
        client._request(
            "GET", "/endpoint",
            absolute_url="https://evil.example.com/steal",
        )


def test_absolute_url_rejects_scheme_downgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    """absolute_url with http:// against an https:// base_url must be
    rejected to prevent transport-security downgrade."""
    response = _StubResponse(status_code=200, json_data={})
    client = _make_client_with_response(monkeypatch, response)

    with pytest.raises(ValueError, match="same origin"):
        client._request(
            "GET", "/endpoint",
            absolute_url="http://example.test/callback",
        )


def test_absolute_url_accepts_same_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """absolute_url targeting the same host as base_url should succeed."""
    response = _StubResponse(status_code=200, json_data={"ok": True})
    client = _make_client_with_response(monkeypatch, response)

    result = client._request(
        "GET", "/endpoint",
        absolute_url="https://example.test/oauth-broker/callback",
    )
    assert result == {"ok": True}


# ========== skip_auth strips Authorization header ==========


def test_skip_auth_strips_authorization_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """When skip_auth=True, the session-level Authorization header must
    not be sent — otherwise stale credentials leak to public endpoints."""
    captured_kwargs: dict = {}

    def _capture_request(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return _StubResponse(status_code=200, json_data={"ok": True})

    client = KamiwazaClient(base_url="https://example.test/api")
    client.session.headers["Authorization"] = "Bearer stale-token"
    monkeypatch.setattr(client.session, "request", _capture_request)

    client._request("GET", "/public-endpoint", skip_auth=True)

    # The per-request headers must set Authorization to None so that
    # requests.Session removes it from the merged result.
    assert captured_kwargs["headers"]["Authorization"] is None


def test_skip_auth_with_none_headers_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Passing headers=None with skip_auth=True must not raise TypeError."""
    captured_kwargs: dict = {}

    def _capture_request(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return _StubResponse(status_code=200, json_data={"ok": True})

    client = KamiwazaClient(base_url="https://example.test/api")
    monkeypatch.setattr(client.session, "request", _capture_request)

    client._request("GET", "/public-endpoint", skip_auth=True, headers=None)

    assert captured_kwargs["headers"]["Authorization"] is None


def test_request_does_not_mutate_caller_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    """_request must not modify the caller-provided headers dict."""
    response = _StubResponse(status_code=200, json_data={"ok": True})
    client = _make_client_with_response(monkeypatch, response)

    caller_headers = {"X-Custom": "value"}
    client._request("GET", "/endpoint", skip_auth=True, headers=caller_headers)

    assert "Authorization" not in caller_headers
