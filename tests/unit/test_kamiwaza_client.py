"""T5.2 / ENG-4678 — Kamiwaza client httpx setup + base _request + retry middleware.

Covers the substantive client surface added on top of the T5.1 skeleton:
    - httpx.Client construction with base_url + Authorization header injection
    - Base ``_request`` helper that returns the parsed JSON body
    - Retry middleware that catches 503 ``psk_propagation_timeout`` per design
      §4.2.1 (exponential backoff: 1, 2, 4, 8, 16s capped at 90s wall-clock)
    - Non-retryable failures surface immediately as KamiwazaError with status

Tests use pytest-httpx's HTTPXMock fixture to intercept httpx requests at
the transport layer — no real network. The retry-timing tests monkeypatch
``time.sleep`` to keep them fast.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest


def test_client_constructs_httpx_client_with_base_url_and_auth() -> None:
    """Kamiwaza.__init__ must build an httpx.Client whose base URL matches
    the constructor arg and whose default Authorization header carries the
    PAT as a Bearer token. T5.2 introduces this — T5.1 only stored the
    values without wiring httpx."""
    from kamiwaza.client import Kamiwaza

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc-123")

    # The httpx.Client is exposed as `_http` (private — convention only,
    # advanced consumers can reach it but the public surface is _request).
    assert client._http is not None
    assert str(client._http.base_url).rstrip("/") == "https://kamiwaza.test"
    auth_header = client._http.headers.get("Authorization")
    assert auth_header == "Bearer pat-abc-123"


def test_request_returns_parsed_json_for_2xx(httpx_mock: Any) -> None:
    """_request must hit the configured base URL, send the Authorization
    header, and return the JSON-decoded body for 2xx responses. Verifies
    the mainline path of the client surface."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/cluster_capabilities",
        json={"gpu_count": 0, "kamiwaza_version": "1.0.0"},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    body = client._request("GET", "/api/cluster/cluster_capabilities")

    assert body == {"gpu_count": 0, "kamiwaza_version": "1.0.0"}

    sent = httpx_mock.get_requests()[0]
    assert sent.headers.get("Authorization") == "Bearer pat-abc"


def test_request_raises_kamiwaza_error_for_4xx(httpx_mock: Any) -> None:
    """Non-retryable client errors (4xx) must surface as KamiwazaError with
    the status code attached so callers can branch by HTTP semantics. T5.10
    layers typed subclasses on top; T5.2 ships the base contract."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.exceptions import KamiwazaError

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/jobs/missing",
        status_code=404,
        json={"detail": "Job not found"},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")

    with pytest.raises(KamiwazaError) as exc_info:
        client._request("GET", "/api/cluster/jobs/missing")

    err = exc_info.value
    assert getattr(err, "status_code", None) == 404


def test_request_retries_psk_propagation_timeout_503(httpx_mock: Any) -> None:
    """Design §4.2.1 SDK retry contract: when the receiver-side pair barrier
    times out it returns 503 with body
    ``{"detail": {"reason": "psk_propagation_timeout", ...}}``. The SDK
    middleware retries on this exact reason and surfaces success once the
    propagation race resolves. Verifies the retry layer kicks in and that
    a successful retry returns the 2xx body."""
    from kamiwaza.client import Kamiwaza

    pair_url = "https://kamiwaza.test/api/cluster/federations/pair"

    # Two propagation-timeout 503s, then success on the third try.
    httpx_mock.add_response(
        method="POST",
        url=pair_url,
        status_code=503,
        json={
            "detail": {
                "reason": "psk_propagation_timeout",
                "elapsed_seconds": 60,
                "remediation": "DataHub PSK propagation timed out.",
            }
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=pair_url,
        status_code=503,
        json={
            "detail": {
                "reason": "psk_propagation_timeout",
                "elapsed_seconds": 60,
                "remediation": "DataHub PSK propagation timed out.",
            }
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=pair_url,
        status_code=200,
        json={"cluster_id": "ORION", "status": "PAIRED"},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")

    with patch("time.sleep") as sleep_mock:
        body = client._request("POST", "/api/cluster/federations/pair", json={})

    assert body == {"cluster_id": "ORION", "status": "PAIRED"}
    assert len(httpx_mock.get_requests()) == 3
    # Backoff should have been exercised twice (between attempts 1→2 and 2→3).
    assert sleep_mock.call_count == 2
    # Verify exponential schedule: 1s, then 2s.
    delays = [call.args[0] for call in sleep_mock.call_args_list]
    assert delays[0] == 1
    assert delays[1] == 2


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_request_retry_gives_up_at_wall_clock_cap(httpx_mock: Any) -> None:
    """Retry middleware must cap total wall-clock at 90s per design §4.2.1.
    Even when the server keeps returning psk_propagation_timeout, the SDK
    eventually surfaces the error rather than retrying forever."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.exceptions import KamiwazaError

    # Add enough responses so the retry loop can't run out of mocks before
    # hitting the wall-clock cap. The retry schedule (1, 2, 4, 8, 16, 32, 64)
    # totals 127s — six retries should exceed the 90s cap.
    pair_url = "https://kamiwaza.test/api/cluster/federations/pair"
    for _ in range(20):
        httpx_mock.add_response(
            method="POST",
            url=pair_url,
            status_code=503,
            json={
                "detail": {
                    "reason": "psk_propagation_timeout",
                    "elapsed_seconds": 60,
                    "remediation": "still racing DataHub.",
                }
            },
        )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")

    # Stub time.sleep so the test runs fast; advance time.monotonic between
    # calls so the wall-clock budget actually drains.
    fake_now = [0.0]

    def fake_monotonic() -> float:
        return fake_now[0]

    def fake_sleep(seconds: float) -> None:
        fake_now[0] += seconds

    with patch("time.monotonic", side_effect=fake_monotonic):
        with patch("time.sleep", side_effect=fake_sleep):
            with pytest.raises(KamiwazaError) as exc_info:
                client._request("POST", "/api/cluster/federations/pair", json={})

    assert getattr(exc_info.value, "status_code", None) == 503
    # Should have exhausted the 90s budget. The schedule 1+2+4+8+16+32+64
    # crosses 90s after the 5th retry (1+2+4+8+16=31, +32=63, +64=127),
    # so we expect 6 attempts (1 initial + 5 retries that fit within budget).
    assert len(httpx_mock.get_requests()) >= 5


def test_request_does_not_retry_other_503_reasons(httpx_mock: Any) -> None:
    """Only 503 with reason='psk_propagation_timeout' is the retryable shape
    in design §4.2.1. Generic 503s ("Secret store unavailable") are real
    server errors that retrying won't fix; surface them immediately."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.exceptions import KamiwazaError

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/jobs",
        status_code=503,
        json={"detail": "Secret store unavailable"},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")

    with pytest.raises(KamiwazaError) as exc_info:
        client._request("GET", "/api/cluster/jobs")

    assert getattr(exc_info.value, "status_code", None) == 503
    # Single attempt — not retried.
    assert len(httpx_mock.get_requests()) == 1


def test_from_env_constructs_via_explicit_constructor(monkeypatch: Any) -> None:
    """T5.2 keeps from_env() behavior consistent with T5.1 — constructor
    args resolved from env, httpx wiring identical."""
    from kamiwaza.client import Kamiwaza

    monkeypatch.setenv("KAMIWAZA_BASE_URL", "https://kamiwaza.test")
    monkeypatch.setenv("KAMIWAZA_TOKEN", "pat-from-env")

    client = Kamiwaza.from_env()

    assert client.base_url == "https://kamiwaza.test"
    assert client.token == "pat-from-env"
    assert client._http.headers.get("Authorization") == "Bearer pat-from-env"


def test_close_releases_httpx_client() -> None:
    """Customers who construct Kamiwaza outside a context manager need to
    free transport resources explicitly. T5.2 wires close() to the
    underlying httpx.Client.close()."""
    from kamiwaza.client import Kamiwaza

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")

    with patch.object(client._http, "close") as close_mock:
        client.close()

    close_mock.assert_called_once()
