from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest
import requests

from kamiwaza_sdk.authentication import Authenticator
from kamiwaza_sdk.client import KamiwazaClient

pytestmark = pytest.mark.unit


class _JSONResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    text = '{"ok": true}'

    def json(self) -> dict[str, bool]:
        return {"ok": True}


class _UnauthorizedResponse:
    status_code = 401
    headers = {"content-type": "application/json"}
    text = '{"detail": "token expired"}'

    def json(self) -> dict[str, str]:
        return {"detail": "token expired"}


class _FalseyAuthenticator(Authenticator):
    def __init__(self) -> None:
        self.authenticate_calls = 0
        self.access_token_calls = 0
        self.refresh_calls = 0

    def __bool__(self) -> bool:
        return False

    def authenticate(self, session: requests.Session) -> None:
        self.authenticate_calls += 1
        session.headers["Authorization"] = "Bearer falsey-token"

    def refresh_token(self, session: requests.Session) -> None:
        self.refresh_calls += 1
        session.headers["Authorization"] = "Bearer refreshed-token"

    def get_access_token(self, session: requests.Session) -> str:
        self.access_token_calls += 1
        return "falsey-token"


def _recording_client(workroom_id: str | None = None) -> KamiwazaClient:
    client = KamiwazaClient(
        base_url="https://example.test/api",
        api_key="test-pat",
        verify=False,
    )
    return client.workroom_scope(workroom_id) if workroom_id is not None else client


def _falsey_client(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[KamiwazaClient, _FalseyAuthenticator]:
    monkeypatch.delenv("KAMIWAZA_API_KEY", raising=False)
    monkeypatch.delenv("KAMIWAZA_API_TOKEN", raising=False)
    authenticator = _FalseyAuthenticator()
    client = KamiwazaClient(
        base_url="https://example.test/api",
        authenticator=authenticator,
        verify=False,
    )
    return client, authenticator


def _record_requests(client: KamiwazaClient) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_request(_method: str, _url: str, **kwargs: Any) -> _JSONResponse:
        calls.append(kwargs)
        return _JSONResponse()

    client.session.request = fake_request  # type: ignore[method-assign]
    return calls


def test_workroom_scope_returns_new_client_and_does_not_mutate_parent() -> None:
    parent = _recording_client()
    parent_calls = _record_requests(parent)
    scoped = parent.workroom_scope("wr-1")
    scoped_calls = _record_requests(scoped)

    scoped.get("/context/health")
    parent.get("/context/health")

    assert scoped is not parent
    assert scoped.base_url == parent.base_url
    assert scoped.authenticator is parent.authenticator
    assert scoped_calls[0]["headers"]["X-Workroom-Id"] == "wr-1"
    assert "X-Workroom-Id" not in parent_calls[0]["headers"]


def test_workroom_scope_can_be_used_as_context_manager() -> None:
    parent = _recording_client()
    closed: list[bool] = []

    with parent.workroom_scope("wr-ctx") as scoped:
        scoped.session.close = lambda: closed.append(True)  # type: ignore[method-assign]
        calls = _record_requests(scoped)
        scoped.get("/context/health")

    assert calls[0]["headers"]["X-Workroom-Id"] == "wr-ctx"
    assert closed == [True]


def test_falsey_supplied_authenticator_authenticates_requests(monkeypatch) -> None:
    client, authenticator = _falsey_client(monkeypatch)
    _record_requests(client)

    client.get("/context/health")

    assert client.authenticator is authenticator
    assert authenticator.authenticate_calls == 1
    assert client.session.headers["Authorization"] == "Bearer falsey-token"


def test_falsey_supplied_authenticator_returns_bearer(monkeypatch) -> None:
    client, authenticator = _falsey_client(monkeypatch)

    assert client.get_bearer_token() == "falsey-token"
    assert authenticator.access_token_calls == 1


def test_falsey_supplied_authenticator_refreshes_after_401(monkeypatch) -> None:
    client, authenticator = _falsey_client(monkeypatch)
    request = Mock(side_effect=[_UnauthorizedResponse(), _JSONResponse()])
    client.session.request = request

    assert client.get("/context/health") == {"ok": True}
    assert authenticator.authenticate_calls == 1
    assert authenticator.refresh_calls == 1
    assert client.session.headers["Authorization"] == "Bearer refreshed-token"
    assert request.call_count == 2


def test_workroom_scope_does_not_close_parent_authenticator() -> None:
    authenticator = Mock()
    parent = KamiwazaClient(
        base_url="https://example.test/api",
        authenticator=authenticator,
        owns_authenticator=True,
        verify=False,
    )

    with parent.workroom_scope("wr-ctx"):
        pass

    authenticator.close.assert_not_called()
    parent.close()
    authenticator.close.assert_called_once_with()


def test_client_closes_owned_authenticator_after_public_replacement() -> None:
    owned = Mock()
    replacement = Mock()
    client = KamiwazaClient(
        base_url="https://example.test/api",
        authenticator=owned,
        owns_authenticator=True,
        verify=False,
    )
    client.authenticator = replacement

    client.close()

    owned.close.assert_called_once_with()
    replacement.close.assert_not_called()


def test_per_request_workroom_header_overrides_scoped_default() -> None:
    scoped = _recording_client("wr-default")
    calls = _record_requests(scoped)

    scoped.get("/context/health", headers={"X-Workroom-ID": "wr-explicit"})

    headers = calls[0]["headers"]
    assert headers["X-Workroom-ID"] == "wr-explicit"
    assert sum(1 for key in headers if key.lower() == "x-workroom-id") == 1


def test_derived_workroom_scopes_do_not_bleed_between_clients() -> None:
    parent = _recording_client()
    left = parent.workroom_scope("wr-left")
    right = parent.workroom_scope("wr-right")
    unscoped = left.workroom_scope(None)

    parent_calls = _record_requests(parent)
    left_calls = _record_requests(left)
    right_calls = _record_requests(right)
    unscoped_calls = _record_requests(unscoped)

    left.get("/context/health")
    right.get("/context/health")
    parent.get("/context/health")
    unscoped.get("/context/health")

    assert left_calls[0]["headers"]["X-Workroom-Id"] == "wr-left"
    assert right_calls[0]["headers"]["X-Workroom-Id"] == "wr-right"
    assert "X-Workroom-Id" not in parent_calls[0]["headers"]
    assert "X-Workroom-Id" not in unscoped_calls[0]["headers"]
