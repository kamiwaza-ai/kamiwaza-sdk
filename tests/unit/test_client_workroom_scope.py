from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest

from kamiwaza_sdk.client import KamiwazaClient

pytestmark = pytest.mark.unit


class _JSONResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    text = '{"ok": true}'

    def json(self) -> dict[str, bool]:
        return {"ok": True}


def _recording_client(workroom_id: str | None = None) -> KamiwazaClient:
    client = KamiwazaClient(
        base_url="https://example.test/api",
        api_key="test-pat",
        verify=False,
    )
    return client.workroom_scope(workroom_id) if workroom_id is not None else client


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


def test_workroom_scope_and_parent_preserve_caller_authenticator() -> None:
    authenticator = Mock()
    parent = KamiwazaClient(
        base_url="https://example.test/api",
        authenticator=authenticator,
        verify=False,
    )

    with parent.workroom_scope("wr-ctx"):
        pass

    authenticator.close.assert_not_called()
    parent.close()
    authenticator.close.assert_not_called()


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
