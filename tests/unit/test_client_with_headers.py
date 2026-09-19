from __future__ import annotations

from typing import Any

import pytest

from kamiwaza_sdk.client import KamiwazaClient

pytestmark = pytest.mark.unit

IDEMPOTENCY_KEY = "8e03978e40d543e8bc936894a57f9324"


class _JSONResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    text = '{"ok": true}'

    def json(self) -> dict[str, bool]:
        return {"ok": True}


def _client() -> KamiwazaClient:
    return KamiwazaClient(
        base_url="https://example.test/api",
        api_key="test-pat",
        verify=False,
    )


def _record_requests(client: KamiwazaClient) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_request(_method: str, _url: str, **kwargs: Any) -> _JSONResponse:
        calls.append(kwargs)
        return _JSONResponse()

    client.session.request = fake_request  # type: ignore[method-assign]
    return calls


def _header(calls: list[dict[str, Any]], name: str) -> list[str]:
    headers = calls[0]["headers"]
    return [value for key, value in headers.items() if key.lower() == name.lower()]


def test_with_headers_sends_the_header_on_every_request() -> None:
    scoped = _client().with_headers({"Idempotency-Key": IDEMPOTENCY_KEY})
    calls = _record_requests(scoped)

    scoped.post("models/")

    assert calls[0]["headers"]["Idempotency-Key"] == IDEMPOTENCY_KEY


def test_with_headers_does_not_mutate_the_receiver() -> None:
    parent = _client()
    parent_calls = _record_requests(parent)

    scoped = parent.with_headers({"Idempotency-Key": IDEMPOTENCY_KEY})
    scoped_calls = _record_requests(scoped)

    parent.post("models/")
    scoped.post("models/")

    assert scoped is not parent
    assert _header(parent_calls, "Idempotency-Key") == []
    assert _header(scoped_calls, "Idempotency-Key") == [IDEMPOTENCY_KEY]
    assert parent._default_headers == {}


def test_with_headers_replaces_an_existing_header_ignoring_case() -> None:
    first = _client().with_headers({"Idempotency-Key": "first-key"})
    second = first.with_headers({"idempotency-key": IDEMPOTENCY_KEY})
    calls = _record_requests(second)

    second.post("models/")

    assert _header(calls, "Idempotency-Key") == [IDEMPOTENCY_KEY]


def test_with_headers_none_value_removes_the_header() -> None:
    scoped = _client().with_headers({"Idempotency-Key": IDEMPOTENCY_KEY})
    cleared = scoped.with_headers({"idempotency-key": None})
    calls = _record_requests(cleared)

    cleared.post("models/")

    assert _header(calls, "Idempotency-Key") == []


def test_with_headers_empty_mapping_returns_an_equivalent_copy() -> None:
    parent = _client().with_headers({"Idempotency-Key": IDEMPOTENCY_KEY})
    copy = parent.with_headers({})
    calls = _record_requests(copy)

    copy.post("models/")

    assert copy is not parent
    assert copy._default_headers == parent._default_headers
    assert _header(calls, "Idempotency-Key") == [IDEMPOTENCY_KEY]


def test_with_headers_keeps_the_workroom_scope_header() -> None:
    scoped = _client().workroom_scope("wr-1").with_headers(
        {"Idempotency-Key": IDEMPOTENCY_KEY}
    )
    calls = _record_requests(scoped)

    scoped.post("models/")

    assert _header(calls, "X-Workroom-Id") == ["wr-1"]
    assert _header(calls, "Idempotency-Key") == [IDEMPOTENCY_KEY]


def test_workroom_scope_still_sends_its_header() -> None:
    scoped = _client().workroom_scope("wr-2")
    calls = _record_requests(scoped)

    scoped.post("models/")

    assert _header(calls, "X-Workroom-Id") == ["wr-2"]


def test_per_call_header_wins_over_the_client_default() -> None:
    scoped = _client().with_headers({"Idempotency-Key": IDEMPOTENCY_KEY})
    calls = _record_requests(scoped)

    scoped.post("models/", headers={"idempotency-key": "per-call-key"})

    assert _header(calls, "Idempotency-Key") == ["per-call-key"]
