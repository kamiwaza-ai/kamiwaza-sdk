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
from kamiwaza_sdk.exceptions import (
    APIError,
    AuthenticationError,
    FederationPairTimeoutError,
)

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
    # Pin jitter to zero so schedules are exactly assertable. Jitter itself is
    # covered by test_applies_jitter_to_the_delay.
    monkeypatch.setattr("kamiwaza_sdk.client._retry_jitter_unit", lambda: 0.0)

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
    # Feed deliberately longer than any plausible schedule so the loop ends by
    # hitting its own bound, not by exhausting the iterator. The previous
    # `len(sleeps) < 12` could never fail: widening the budget died at
    # StopIteration one request earlier than the assertion.
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [_authority_fenced_response() for _ in range(64)],
    )

    with pytest.raises(APIError) as exc_info:
        client.delete("workrooms/abc")

    assert exc_info.value.status_code == 503
    assert "workroom_authority_unavailable" in str(exc_info.value)
    assert sleeps == [10.0] * 6, "exact schedule: six 10s sleeps, then terminal"


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


def test_clamps_an_absurdly_large_hint_to_the_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A huge hint is clamped to the per-attempt ceiling, not obeyed."""
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [_authority_fenced_response(retry_after=999), _success_response()],
    )

    client.delete("workrooms/abc")

    assert sleeps == [30.0]


def test_rejects_a_zero_hint_rather_than_hot_spinning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``retry_after_seconds: 0`` carries no usable delay and must not retry."""
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [
            _StubResponse(
                status_code=503,
                json_data={
                    "code": "workroom_authority_unavailable",
                    "retry_after_seconds": 0,
                },
            )
        ],
    )

    with pytest.raises(APIError):
        client.delete("workrooms/abc")

    assert sleeps == []


def test_rejects_a_boolean_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """``True`` is an int in Python; it is not a delay."""
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [
            _StubResponse(
                status_code=503,
                json_data={
                    "code": "workroom_authority_unavailable",
                    "retry_after_seconds": True,
                },
            )
        ],
    )

    with pytest.raises(APIError):
        client.delete("workrooms/abc")

    assert sleeps == []


def test_floors_a_tiny_hint_to_prevent_amplification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sub-second hint is raised to the floor.

    Unfloored, a hostile ``0.0001`` sustains ~1712 req/s against the customer's
    own cluster for the whole budget.
    """
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [_authority_fenced_response(retry_after=0.0001), _success_response()],
    )

    client.delete("workrooms/abc")

    assert sleeps == [1.0]


def test_caps_attempts_independently_of_the_wall_clock_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A small hint must not spend the budget in thousands of requests."""
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [_authority_fenced_response(retry_after=1) for _ in range(64)],
    )

    with pytest.raises(APIError):
        client.delete("workrooms/abc")

    assert sleeps == [1.0] * 6, "attempt cap bounds the count, not just the time"


def test_applies_jitter_to_the_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Co-fenced clients get the same hint; jitter keeps them out of lockstep."""
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [_authority_fenced_response(), _success_response()],
    )
    monkeypatch.setattr("kamiwaza_sdk.client._retry_jitter_unit", lambda: 1.0)

    client.delete("workrooms/abc")

    assert sleeps == [11.0], "10s hint + 10% jitter at the top of the range"


def test_does_not_replay_an_open_file_passed_as_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The ``data=`` half of the guard must be pinned too.

    Dropping ``"data"`` from the guarded kwargs previously passed the whole
    suite, silently re-opening the fixed data-integrity bug.
    """
    target = tmp_path / "payload.bin"
    target.write_bytes(b"payload")
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [_authority_fenced_response(), _success_response()],
    )

    with target.open("rb") as handle, pytest.raises(APIError):
        client._request("POST", "context/upload", data=handle)

    assert sleeps == []


def test_does_not_replay_a_generator_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """A one-shot iterator is spent after the first attempt.

    Pins the walker's ``__iter__`` branch, which the file-object test cannot --
    a BytesIO satisfies both ``.read`` and ``__iter__``, so either could rot
    unnoticed behind the other.
    """
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [_authority_fenced_response(), _success_response()],
    )

    def chunks():
        yield b"a"
        yield b"b"

    with pytest.raises(APIError):
        client._request("POST", "context/upload", data=chunks())

    assert sleeps == []


def test_wall_clock_budget_bounds_long_delays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the budget itself, which the attempt cap otherwise masks.

    At the 30s ceiling the budget binds first (2 sleeps), so widening it moves
    this schedule -- whereas any hint small enough to hit the cap makes the
    budget invisible.
    """
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [_authority_fenced_response(retry_after=30) for _ in range(64)],
    )

    with pytest.raises(APIError):
        client.delete("workrooms/abc")

    assert sleeps == [30.0, 30.0], "60s budget admits exactly two 30s sleeps"


def test_does_not_replay_a_read_only_body_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the walker's ``.read`` branch on its own.

    Files and BytesIO satisfy *both* ``.read`` and ``__iter__``, so a test using
    either lets one branch rot behind the other. This object has only ``read``.
    """

    class _ReadOnlyBody:
        def read(self, *_args: Any) -> bytes:
            return b"payload"

    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [_authority_fenced_response(), _success_response()],
    )

    with pytest.raises(APIError):
        client._request("POST", "context/upload", data=_ReadOnlyBody())

    assert sleeps == []


def _psk_timeout_response() -> _StubResponse:
    return _StubResponse(
        status_code=503,
        json_data={"detail": {"reason": "psk_propagation_timeout"}},
    )


def test_psk_arm_does_not_replay_a_streamed_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The psk retry shares the loop and the same replay hazard (H1).

    Pre-existing, but fixed here because it is the same defect class in the
    same ``while True`` and the remedy was already in the diff.
    """
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [_psk_timeout_response(), _success_response()],
    )

    def chunks():
        yield b"a"

    with pytest.raises(FederationPairTimeoutError):
        client._request("POST", "federation/pair", data=chunks())

    assert sleeps == [], "a spent stream must not be replayed on the psk arm"


def test_401_arm_raises_rather_than_replaying_a_streamed_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token refresh mid-upload must not re-send a consumed stream (H1).

    The refresh itself is stubbed to isolate the replay decision; what is under
    test is what ``_request`` does *after* a successful refresh.
    """
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [_StubResponse(status_code=401, json_data={}), _success_response()],
    )
    monkeypatch.setattr(client, "_handle_unauthorized_response", lambda *_a, **_k: None)

    def chunks():
        yield b"a"

    with pytest.raises(AuthenticationError) as exc_info:
        client._request("POST", "context/upload", data=chunks())

    assert "already been consumed" in str(exc_info.value)
    assert sleeps == []


# Codes admitted in ENG-10516. The delay column is a representative in-band
# value, NOT a claim about core's cadence: two of these are computed at
# runtime rather than literals (embedding_deploying forwards the provisioner's
# variable warmup window; embedding_runtime_unreachable forwards the upstream
# runtime's own header), and asserting a number the SDK was just handed pins
# nothing about core anyway. What is pinned here is that each code is admitted
# and its in-band hint is slept verbatim; the clamps are pinned separately.
_ADMITTED_CODES = [
    ("embedding_deploying", 10),
    ("embedding_runtime_unreachable", 5),
    ("discovery_unavailable", 30),
    ("pinned_discovery_unavailable", 30),
    ("global_ontology_provisioning", 30),
]

# Deliberately excluded: core emits these from the same responses={503: ...}
# entries as the admitted codes, so they are the meaningful boundary.
_EXCLUDED_CODES = ["embedding_unavailable", "embedding_service_error"]


@pytest.mark.parametrize(("code", "retry_after"), _ADMITTED_CODES)
def test_retries_each_admitted_503_code(
    monkeypatch: pytest.MonkeyPatch, code: str, retry_after: int
) -> None:
    """Each vetted code retries, sleeping the delay core actually sends.

    Bodies mirror ``ServiceUnavailable503Detail`` exactly — bare top-level
    keys, not nested under ``detail`` — because that is what
    ``BareDetailHTTPException`` puts on the wire. Every value sits inside the
    [1s, 30s] band so neither clamp masks it.

    Driven over POST: no core route emits any of these on a DELETE. The
    machinery is verb-agnostic, so the verb proves nothing either way — but
    the test should not read as though it does.
    """
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [
            _StubResponse(
                status_code=503,
                json_data={
                    "code": code,
                    "message": f"{code} is transient",
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            ),
            _success_response(),
        ],
    )

    result = client.post("probe")

    assert result == {"deleted": True}
    assert sleeps == [float(retry_after)]


@pytest.mark.parametrize("code", _EXCLUDED_CODES)
def test_does_not_retry_a_real_excluded_core_code(
    monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    """The allowlist boundary, pinned against codes core actually emits.

    The synthetic-code test proves the mechanism rejects unknown values; this
    proves the *membership* is right. These two ship from the same
    ``responses={503: ...}`` entries as the admitted codes, so a careless
    widening would pick them up.
    """
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [
            _StubResponse(
                status_code=503,
                json_data={
                    "code": code,
                    "message": f"{code} is not admitted",
                    "retry_after_seconds": 5,
                },
            )
        ],
    )

    with pytest.raises(APIError):
        client.post("probe")

    assert sleeps == []
