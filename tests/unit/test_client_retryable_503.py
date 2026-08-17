"""Retryable-503 middleware — honor the server's ``Retry-After`` contract.

``kamiwaza.lib.http_errors.service_unavailable()`` builds every retryable
503 with the bare ``ServiceUnavailable503Detail`` body::

    {"code": ..., "message": ..., "retry_after_seconds": N}

and sets the matching ``Retry-After: N`` header. Its docstring is explicit
that this shape means "transient unavailability that callers should retry".
Nothing in the SDK honored it: the response fell straight through to
``_raise_for_error_response`` and surfaced as ``APIError``, so a workroom
that was briefly fenced failed the caller outright.

Observed in the offline ad-hoc SDK e2e suite as
``workroom_authority_unavailable`` on workroom delete
(``kamiwaza_sdk/client.py:510``).

Contract asserted here:

- Trigger: HTTP 503 whose JSON body carries ``retry_after_seconds``.
- Delay: the server's own hint, per attempt.
- Bound: capped attempts and a wall-clock budget, so a wedged fence cannot
  hang the SDK.
- Exhaustion: the ordinary ``APIError`` for that response — unchanged
  terminal behavior for callers already handling it.
- Non-trigger: a 503 without the hint keeps raising immediately.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from typing import Any

import pytest
from kamiwaza_sdk.client import KamiwazaClient
from kamiwaza_sdk.exceptions import APIError

pytestmark = pytest.mark.unit


class _StubResponse:
    """Minimal requests.Response-compatible stub for sequential-retry tests."""

    def __init__(
        self,
        *,
        status_code: int,
        json_data: object | None = None,
        text: str = "",
        content_type: str = "application/json",
        headers: dict | None = None,
    ) -> None:
        self.status_code = status_code
        # Real responses always carry the serialized body in .text; the SDK
        # builds its error message from it. Default to the JSON so assertions
        # about the surfaced message reflect production.
        self.text = text or (json.dumps(json_data) if json_data is not None else "")
        self.headers = {"content-type": content_type}
        if headers:
            self.headers.update(headers)
        self._json_data = json_data

    def json(self) -> object:
        if self._json_data is None:
            raise ValueError("No JSON payload")
        return self._json_data


def _make_client_with_sequence(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[_StubResponse],
) -> tuple[KamiwazaClient, list[float]]:
    """Client whose session.request replays ``responses`` in order, with
    deterministic monotonic/sleep so the schedule is assertable without waits."""
    client = KamiwazaClient(base_url="https://example.test/api")
    iterator: Iterator[_StubResponse] = iter(responses)

    def fake_request(*_args: Any, **_kwargs: Any) -> _StubResponse:
        return next(iterator)

    monkeypatch.setattr(client.session, "request", fake_request)

    fake_now = [0.0]
    sleeps: list[float] = []

    def fake_monotonic() -> float:
        return fake_now[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        fake_now[0] += seconds

    monkeypatch.setattr("kamiwaza_sdk.client.time.monotonic", fake_monotonic)
    monkeypatch.setattr("kamiwaza_sdk.client.time.sleep", fake_sleep)

    return client, sleeps


def _authority_fenced_response(retry_after: int = 10) -> _StubResponse:
    """The exact body kamiwaza's workroom authority-fence handler returns."""
    return _StubResponse(
        status_code=503,
        json_data={
            "code": "workroom_authority_unavailable",
            "message": (
                "The workroom is briefly locked by another operation. "
                "Retry the request."
            ),
            "retry_after_seconds": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )


def _success_response() -> _StubResponse:
    return _StubResponse(status_code=200, json_data={"deleted": True})


def test_retries_fenced_workroom_delete_until_it_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A briefly-fenced workroom resolves on retry instead of raising."""
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [_authority_fenced_response(), _success_response()],
    )

    result = client.delete("workrooms/abc")

    assert result == {"deleted": True}
    assert sleeps == [10.0]


def test_honors_the_servers_retry_after_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The delay is the server's value, not a fixed local schedule."""
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [_authority_fenced_response(retry_after=3), _success_response()],
    )

    client.delete("workrooms/abc")

    assert sleeps == [3.0]


def test_gives_up_and_raises_api_error_when_fence_never_clears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wedged fence terminates as the ordinary APIError rather than
    retrying forever — callers already handling APIError are unaffected."""
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [_authority_fenced_response() for _ in range(12)],
    )

    with pytest.raises(APIError) as exc_info:
        client.delete("workrooms/abc")

    assert exc_info.value.status_code == 503
    assert "workroom_authority_unavailable" in str(exc_info.value)
    assert sleeps, "must actually retry before giving up"
    assert len(sleeps) < 12, "must stop retrying, not exhaust the response feed"


def test_does_not_retry_a_503_without_a_retry_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absent the hint, a 503 is not declared retryable — unchanged behavior."""
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [
            _StubResponse(
                status_code=503,
                json_data={"code": "workroom_authority_unavailable"},
            )
        ],
    )

    with pytest.raises(APIError):
        client.delete("workrooms/abc")

    assert sleeps == []


def test_does_not_retry_an_unlisted_503_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hint alone is not consent to re-issue the call (ENG-10506).

    Retrying is a claim about idempotency, so only codes we have explicitly
    vetted are retried — an unknown code with a valid hint stays terminal.
    """
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [
            _StubResponse(
                status_code=503,
                json_data={
                    "code": "some_other_service_unavailable",
                    "message": "not vetted for replay",
                    "retry_after_seconds": 5,
                },
            )
        ],
    )

    with pytest.raises(APIError):
        client.delete("workrooms/abc")

    assert sleeps == []


def test_does_not_retry_non_503_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 409 carrying a stray retry hint is still terminal."""
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [
            _StubResponse(
                status_code=409,
                json_data={
                    "code": "workroom_authority_unavailable",
                    "retry_after_seconds": 5,
                },
            )
        ],
    )

    with pytest.raises(APIError):
        client.delete("workrooms/abc")

    assert sleeps == []


def test_does_not_replay_a_request_whose_body_is_a_consumed_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file-like body cannot be re-sent (PR #276 review, P1).

    ``requests`` reads the stream to EOF on the first attempt. Replaying the
    same kwargs would re-encode it from its current position and silently
    upload an empty or truncated file -- ``ContextService.upload_file`` takes
    ``IO[bytes]``, so this is reachable. Better to surface the 503 than to
    write corrupt data.
    """
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [_authority_fenced_response(), _success_response()],
    )

    with pytest.raises(APIError):
        client._request(
            "POST",
            "context/upload",
            files={"file": ("x.bin", io.BytesIO(b"payload"))},
        )

    assert sleeps == [], "a stream body must not be replayed"


def test_still_replays_ordinary_json_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stream guard must not disable retry for normal repeatable bodies."""
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [_authority_fenced_response(), _success_response()],
    )

    result = client._request("POST", "workrooms", json={"name": "w"})

    assert result == {"deleted": True}
    assert sleeps == [10.0]
