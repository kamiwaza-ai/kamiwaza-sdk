"""T5.10 / ENG-4681 — Skeleton typed exception hierarchy.

Verifies the federation-specific exception types per design §4.2.11 and
that ``_to_kamiwaza_error`` (the response → exception mapper used by
``_request``) dispatches the correct subclass based on response shape.

Each typed subclass:
  - Inherits from KamiwazaError
  - Carries the same status_code / body fields T5.2 introduced
  - Is catchable via the base KamiwazaError for catch-all handling
"""

from __future__ import annotations

from typing import Any

import pytest


def test_typed_exceptions_subclass_kamiwaza_error() -> None:
    """All federation-aware exceptions inherit from KamiwazaError so
    customer code can catch the base class for non-specific handling."""
    from kamiwaza.exceptions import (
        BrokeredUserNotAllowlistedError,
        FederationPairTimeoutError,
        KamiwazaError,
        MeshJobFailedError,
        MeshJobTimeoutError,
        NativeRealmRequiredError,
    )

    typed = [
        FederationPairTimeoutError,
        BrokeredUserNotAllowlistedError,
        MeshJobTimeoutError,
        MeshJobFailedError,
        NativeRealmRequiredError,
    ]
    for cls in typed:
        assert issubclass(cls, KamiwazaError), (
            f"{cls.__name__} must subclass KamiwazaError"
        )


def test_typed_exceptions_carry_status_code_and_body() -> None:
    """Typed subclasses preserve the status_code + body kwargs from the
    base class so handlers can inspect server-provided detail."""
    from kamiwaza.exceptions import FederationPairTimeoutError

    err = FederationPairTimeoutError(
        "psk propagation timeout",
        status_code=503,
        body={
            "detail": {
                "reason": "psk_propagation_timeout",
                "elapsed_seconds": 60,
            }
        },
    )
    assert err.status_code == 503
    assert err.body == {
        "detail": {"reason": "psk_propagation_timeout", "elapsed_seconds": 60}
    }


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_request_raises_brokered_user_not_allowlisted_on_403(
    httpx_mock: Any,
) -> None:
    """ext-authz returns 403 with body
    ``{"detail": {"reason": "brokered_user_not_allowlisted"}}`` when a
    brokered user attempts a mesh request without being on the receiver's
    allowlist. SDK must surface this as the typed subclass so customer
    code can pattern-match on the membership-error class specifically."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.exceptions import (
        BrokeredUserNotAllowlistedError,
        KamiwazaError,
    )

    httpx_mock.add_response(
        method="POST",
        url="https://kamiwaza.test/api/cluster/jobs/run",
        status_code=403,
        json={"detail": {"reason": "brokered_user_not_allowlisted"}},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")

    with pytest.raises(BrokeredUserNotAllowlistedError) as exc_info:
        client._request("POST", "/api/cluster/jobs/run", json={})

    err = exc_info.value
    assert isinstance(err, KamiwazaError)
    assert err.status_code == 403


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_request_raises_native_realm_required_on_403(httpx_mock: Any) -> None:
    """High-stakes endpoints (gate binding, federation pair, federation
    user revoke, federation disconnect) refuse mesh-origin requests with
    body ``{"detail": {"reason": "endpoint_requires_native_realm"}}``."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.exceptions import NativeRealmRequiredError

    httpx_mock.add_response(
        method="PUT",
        url="https://kamiwaza.test/api/cluster/execution-gate",
        status_code=403,
        json={"detail": {"reason": "endpoint_requires_native_realm"}},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")

    with pytest.raises(NativeRealmRequiredError) as exc_info:
        client._request("PUT", "/api/cluster/execution-gate", json={})

    assert exc_info.value.status_code == 403


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_request_raises_federation_pair_timeout_after_retry_budget(
    httpx_mock: Any,
) -> None:
    """When the retry middleware exhausts its 90s wall-clock budget on
    psk_propagation_timeout 503s, the surfaced exception should be the
    typed FederationPairTimeoutError (not the generic KamiwazaError)
    so customer code can branch on this specific terminal failure."""
    from unittest.mock import patch

    from kamiwaza.client import Kamiwaza
    from kamiwaza.exceptions import FederationPairTimeoutError

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
                    "remediation": "DataHub still racing.",
                }
            },
        )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")

    fake_now = [0.0]

    def fake_monotonic() -> float:
        return fake_now[0]

    def fake_sleep(seconds: float) -> None:
        fake_now[0] += seconds

    with patch("time.monotonic", side_effect=fake_monotonic):
        with patch("time.sleep", side_effect=fake_sleep):
            with pytest.raises(FederationPairTimeoutError) as exc_info:
                client._request("POST", "/api/cluster/federations/pair", json={})

    assert exc_info.value.status_code == 503


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_generic_5xx_falls_back_to_kamiwaza_error(httpx_mock: Any) -> None:
    """Responses without a typed reason (e.g. 500 Internal Server Error,
    503 Secret store unavailable) fall back to the base KamiwazaError —
    typed subclasses must not be invented for unrecognized shapes."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.exceptions import (
        BrokeredUserNotAllowlistedError,
        FederationPairTimeoutError,
        KamiwazaError,
    )

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/jobs",
        status_code=500,
        json={"detail": "Something exploded"},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")

    with pytest.raises(KamiwazaError) as exc_info:
        client._request("GET", "/api/cluster/jobs")

    err = exc_info.value
    # Specifically NOT a federation-typed subclass.
    assert not isinstance(err, FederationPairTimeoutError)
    assert not isinstance(err, BrokeredUserNotAllowlistedError)
    assert err.status_code == 500


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_403_without_typed_reason_uses_base_class(httpx_mock: Any) -> None:
    """A 403 with an unrecognized reason (or no reason field) is a
    permission denial we don't have a specific class for — surface as
    KamiwazaError, not a fabricated typed subclass."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.exceptions import (
        BrokeredUserNotAllowlistedError,
        KamiwazaError,
        NativeRealmRequiredError,
    )

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/jobs",
        status_code=403,
        json={"detail": "Forbidden"},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")

    with pytest.raises(KamiwazaError) as exc_info:
        client._request("GET", "/api/cluster/jobs")

    err = exc_info.value
    assert not isinstance(err, BrokeredUserNotAllowlistedError)
    assert not isinstance(err, NativeRealmRequiredError)
    assert err.status_code == 403
