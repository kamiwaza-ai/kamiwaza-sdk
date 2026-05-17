"""T7.4 / ENG-5038 — psk_propagation_timeout retry middleware on the
canonical KamiwazaClient.

WS-M3.2 foundation. Ports the design §4.2.1 retry contract from the
httpx-based kamiwaza/client.py into kamiwaza_sdk/client.py using
requests.Session semantics. Same contract:

- Trigger: HTTP 503 with body ``{"detail": {"reason": "psk_propagation_timeout"}}``
- Schedule: exponential backoff (1, 2, 4, 8, 16, 32, 64 seconds)
- Wall-clock cap: 90 seconds total
- Exhaustion: raise FederationPairTimeoutError (typed)

Non-trigger responses (200, 4xx, 503 with other reason) follow the
existing request path (200 returns immediately; 4xx/5xx raise APIError
or typed subclass via _raise_for_error_response).
"""

from __future__ import annotations

from typing import Any, Iterator, List, Optional

import pytest

from kamiwaza_sdk.client import KamiwazaClient
from kamiwaza_sdk.exceptions import FederationPairTimeoutError

pytestmark = pytest.mark.unit


class _StubResponse:
    """Minimal requests.Response-compatible stub for sequential-retry tests."""

    def __init__(
        self,
        *,
        status_code: int,
        json_data: Optional[object] = None,
        text: str = "",
        content_type: str = "application/json",
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = {"content-type": content_type}
        self._json_data = json_data

    def json(self) -> object:
        if self._json_data is None:
            raise ValueError("No JSON payload")
        return self._json_data


def _make_client_with_sequence(
    monkeypatch: pytest.MonkeyPatch,
    responses: List[_StubResponse],
) -> tuple[KamiwazaClient, list[float]]:
    """Build a client whose session.request returns the given responses in
    order. Also installs deterministic time.monotonic + time.sleep so the
    retry schedule can be asserted without real waits.

    Returns: (client, sleeps_list) where sleeps_list captures every
    duration passed to time.sleep during the test.
    """
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


def _psk_timeout_response() -> _StubResponse:
    return _StubResponse(
        status_code=503,
        json_data={
            "detail": {
                "reason": "psk_propagation_timeout",
                "elapsed_seconds": 30,
                "remediation": "DataHub still racing.",
            }
        },
    )


def _success_response() -> _StubResponse:
    return _StubResponse(
        status_code=200,
        json_data={"id": "fed-1", "status": "PAIRED"},
    )


def test_psk_retry_succeeds_on_eventual_2xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After 3 psk_propagation_timeout 503s, a 200 ends the retry loop and
    returns the parsed body. Schedule is exact: 1, 2, 4 seconds."""
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [
            _psk_timeout_response(),
            _psk_timeout_response(),
            _psk_timeout_response(),
            _success_response(),
        ],
    )
    result = client.get("/cluster/federations/abc/pair")
    assert isinstance(result, dict)
    assert result["status"] == "PAIRED"
    assert sleeps == [1.0, 2.0, 4.0]


def test_psk_retry_exhausts_schedule_and_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When 503 psk_propagation_timeout persists past the 90s wall-clock
    cap, the SDK surfaces FederationPairTimeoutError (not the generic
    APIError) so customer code can branch on the terminal failure."""
    # 8 timeouts cover the full schedule (1+2+4+8+16+32=63 < 90; +64 = 127
    # would push past the cap, so deadline check fires on the 64s entry).
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [_psk_timeout_response()] * 20,  # plenty
    )

    with pytest.raises(FederationPairTimeoutError) as exc_info:
        client.get("/cluster/federations/abc/pair")

    err = exc_info.value
    assert err.status_code == 503
    # Body carries the structured detail so caller can inspect elapsed/remediation.
    assert isinstance(err.body, dict)
    detail = err.body.get("detail")
    assert isinstance(detail, dict)
    assert detail.get("reason") == "psk_propagation_timeout"
    # Schedule that fits within 90s wall-clock cap: 1+2+4+8+16+32 = 63s
    # (the next 64s sleep would exceed 90 - 63 = 27s remaining and trip
    # the deadline check). Exact assertion on the slept-durations.
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0]


def test_psk_retry_does_not_trigger_on_503_with_other_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 503 whose detail.reason is something else (e.g. ServiceUnavailable
    from a DataHub error) must NOT trigger psk retry — surfaces immediately
    as APIError per the existing 5xx path."""
    from kamiwaza_sdk.exceptions import APIError

    other_503 = _StubResponse(
        status_code=503,
        json_data={"detail": {"reason": "service_unavailable"}},
    )
    client, sleeps = _make_client_with_sequence(monkeypatch, [other_503])

    with pytest.raises(APIError):
        client.get("/cluster/federations")

    assert sleeps == [], "No retry sleeps should fire for non-PSK 503s"


def test_psk_retry_does_not_trigger_on_non_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4xx errors don't trigger psk retry — surfaces immediately."""
    from kamiwaza_sdk.exceptions import APIError

    forbidden = _StubResponse(
        status_code=403,
        json_data={"detail": {"reason": "psk_propagation_timeout"}},
        # ^ pathological body — reason matches but status doesn't.
        # Per design §4.2.1, status_code is part of the match.
    )
    client, sleeps = _make_client_with_sequence(monkeypatch, [forbidden])

    with pytest.raises(APIError):
        client.get("/cluster/federations")

    assert sleeps == [], "No retry sleeps should fire for non-503 statuses"


def test_psk_retry_uses_exponential_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first 5 retries follow the exact schedule (1, 2, 4, 8, 16
    seconds). Off-by-one in the schedule would defeat the design's intent
    of allowing DataHub up to ~60s of total back-off in the common case."""
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [_psk_timeout_response()] * 5 + [_success_response()],
    )
    client.get("/cluster/federations/x/pair")
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0]


def test_psk_retry_caps_wall_clock_at_90_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wall-clock cap is the load-bearing safety — without it a customer
    pinning to a stuck cluster could hang the SDK for 1+2+4+...+64 = 127s.
    The deadline check before each sleep ensures total elapsed stays ≤90s."""
    client, sleeps = _make_client_with_sequence(
        monkeypatch,
        [_psk_timeout_response()] * 20,
    )

    with pytest.raises(FederationPairTimeoutError):
        client.get("/cluster/federations/x/pair")

    # Total slept time must be ≤ 90s (the wall-clock budget).
    total_slept = sum(sleeps)
    assert total_slept <= 90.0, f"Total slept {total_slept}s exceeds 90s wall-clock cap"
    # And the last sleep must NOT have been the 64s entry — that would have
    # pushed total past 90s (1+2+4+8+16+32+64 = 127).
    assert 64.0 not in sleeps


def test_psk_retry_preserves_existing_401_refresh_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adding psk retry to _request must NOT regress the existing 401
    refresh logic — a 401 still goes through the auth-refresh path, not
    the psk retry path."""
    # Force a 401 (no authenticator configured → should raise authn error
    # rather than retry indefinitely).
    from kamiwaza_sdk.exceptions import AuthenticationError

    unauthorized = _StubResponse(
        status_code=401,
        json_data={"detail": "Unauthorized"},
    )
    client, sleeps = _make_client_with_sequence(monkeypatch, [unauthorized])
    client.authenticator = None  # No refresh path available.

    with pytest.raises(AuthenticationError):
        client.get("/cluster/federations")

    assert sleeps == [], "PSK retry must not engage on 401"
