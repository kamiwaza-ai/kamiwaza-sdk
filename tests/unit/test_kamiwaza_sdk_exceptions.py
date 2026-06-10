"""T7.2 / ENG-5036 — Federation-aware typed exception hierarchy on the
canonical ``kamiwaza_sdk.exceptions`` surface.

Verifies that the M1+ federation-aware typed subclasses (previously at
``kamiwaza/exceptions.py``) have migrated to ``kamiwaza_sdk/exceptions.py``
per design v0.3.7 §4.2.11. Tests are written against the canonical surface
and explicitly do NOT touch the deprecation shim (T7.14 will add a
separate shim smoke test).

Per WS-M3.2 design (§6.2 v0.3.7), the unified hierarchy is:

    KamiwazaError                     (base — extended with status_code + body kwargs)
        ├─ APIError                   (existing legacy)
        ├─ AuthenticationError        (existing legacy)
        ├─ AuthorizationError         (existing legacy)
        │      └─ NativeRealmRequiredError      (NEW M3.2)
        │      └─ BrokeredUserNotAllowlistedError (NEW M3.2)
        ├─ NotFoundError              (existing legacy)
        ├─ ValidationError            (existing legacy)
        ├─ TimeoutError               (existing legacy)
        ├─ FederationPairTimeoutError (NEW M3.2)
        ├─ MeshJobTimeoutError        (NEW M3.2)
        └─ MeshJobFailedError         (NEW M3.2)

Plus ``error_for_response`` dispatch function ported from ``kamiwaza/client.py``.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Base class extension — backwards-compatible kwargs
# ---------------------------------------------------------------------------


def test_kamiwaza_error_backwards_compat_with_message_only() -> None:
    """Existing kamiwaza_sdk callers do ``raise KamiwazaError("boom")``.
    That MUST keep working — the M3.2 extension adds kwargs without breaking
    the positional-only callers."""
    from kamiwaza_sdk.exceptions import KamiwazaError

    err = KamiwazaError("boom")
    assert str(err) == "boom"
    # Existing .message attribute preserved.
    assert err.message == "boom"


def test_kamiwaza_error_accepts_status_code_and_body() -> None:
    """T7.2 extends the base class with M1+ kwargs so callers can carry
    server response context without a separate APIError subclass."""
    from kamiwaza_sdk.exceptions import KamiwazaError

    err = KamiwazaError(
        "psk propagation timeout",
        status_code=503,
        body={"detail": {"reason": "psk_propagation_timeout", "elapsed_seconds": 60}},
    )
    assert err.status_code == 503
    assert err.body == {
        "detail": {"reason": "psk_propagation_timeout", "elapsed_seconds": 60}
    }


def test_kamiwaza_error_status_code_and_body_default_none() -> None:
    """Default values for status_code + body are None when not supplied
    (covers the 'local error, no HTTP context' case)."""
    from kamiwaza_sdk.exceptions import KamiwazaError

    err = KamiwazaError("config error")
    assert err.status_code is None
    assert err.body is None


# ---------------------------------------------------------------------------
# Federation-aware typed subclasses present + correctly inheriting
# ---------------------------------------------------------------------------


def test_federation_aware_subclasses_inherit_from_kamiwaza_error() -> None:
    """All 5 federation-aware subclasses must subclass KamiwazaError so
    customer code can catch the base class for non-specific handling."""
    from kamiwaza_sdk.exceptions import (
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


def test_typed_subclasses_carry_kwargs() -> None:
    """Typed subclasses must accept the same status_code + body kwargs
    as the base — otherwise the dispatch function can't construct them."""
    from kamiwaza_sdk.exceptions import (
        BrokeredUserNotAllowlistedError,
        FederationPairTimeoutError,
        MeshJobFailedError,
        MeshJobTimeoutError,
        NativeRealmRequiredError,
    )

    body = {"detail": {"reason": "x"}}
    for cls in [
        FederationPairTimeoutError,
        BrokeredUserNotAllowlistedError,
        MeshJobTimeoutError,
        MeshJobFailedError,
        NativeRealmRequiredError,
    ]:
        err = cls(f"{cls.__name__} msg", status_code=503, body=body)
        assert err.status_code == 503
        assert err.body == body


def test_authorization_subclasses_inherit_correctly() -> None:
    """Per design §4.2.11, NativeRealmRequiredError and
    BrokeredUserNotAllowlistedError are AuthorizationError sub-types (both
    surface as HTTP 403 from the server). Customers catching
    AuthorizationError should catch both."""
    from kamiwaza_sdk.exceptions import (
        AuthorizationError,
        BrokeredUserNotAllowlistedError,
        NativeRealmRequiredError,
    )

    assert issubclass(NativeRealmRequiredError, AuthorizationError)
    assert issubclass(BrokeredUserNotAllowlistedError, AuthorizationError)


# ---------------------------------------------------------------------------
# error_for_response dispatch function — moved from kamiwaza/client.py
# ---------------------------------------------------------------------------


def test_error_for_response_dispatches_psk_propagation_timeout() -> None:
    """503 with detail.reason=psk_propagation_timeout dispatches to
    FederationPairTimeoutError per the design's retry contract (§4.2.1)."""
    from kamiwaza_sdk.exceptions import (
        FederationPairTimeoutError,
        error_for_response,
    )

    body = {
        "detail": {
            "reason": "psk_propagation_timeout",
            "elapsed_seconds": 60,
            "remediation": "DataHub still racing.",
        }
    }
    err = error_for_response(503, body, "Pair barrier timed out")
    assert isinstance(err, FederationPairTimeoutError)
    assert err.status_code == 503
    assert err.body == body


def test_error_for_response_dispatches_brokered_user_not_allowlisted() -> None:
    """403 with detail.reason=brokered_user_not_allowlisted → typed subclass."""
    from kamiwaza_sdk.exceptions import (
        BrokeredUserNotAllowlistedError,
        error_for_response,
    )

    body = {"detail": {"reason": "brokered_user_not_allowlisted"}}
    err = error_for_response(403, body, "Brokered user denied")
    assert isinstance(err, BrokeredUserNotAllowlistedError)
    assert err.status_code == 403


def test_error_for_response_dispatches_endpoint_requires_native_realm() -> None:
    """403 with detail.reason=endpoint_requires_native_realm → typed subclass."""
    from kamiwaza_sdk.exceptions import (
        NativeRealmRequiredError,
        error_for_response,
    )

    body = {"detail": {"reason": "endpoint_requires_native_realm"}}
    err = error_for_response(403, body, "Mesh-origin refused")
    assert isinstance(err, NativeRealmRequiredError)
    assert err.status_code == 403


def test_error_for_response_falls_back_to_base_for_unrecognized_shape() -> None:
    """A response with no recognized (status_code, reason) pair falls back
    to KamiwazaError — typed subclasses must NOT be invented for shapes we
    don't own."""
    from kamiwaza_sdk.exceptions import (
        BrokeredUserNotAllowlistedError,
        FederationPairTimeoutError,
        KamiwazaError,
        NativeRealmRequiredError,
        error_for_response,
    )

    # 500 — no typed mapping
    err = error_for_response(500, {"detail": "Something exploded"}, "Server error")
    assert isinstance(err, KamiwazaError)
    assert not isinstance(err, FederationPairTimeoutError)
    assert not isinstance(err, BrokeredUserNotAllowlistedError)
    assert not isinstance(err, NativeRealmRequiredError)
    assert err.status_code == 500


def test_error_for_response_falls_back_on_403_without_reason() -> None:
    """A 403 with detail as a string (not a dict with reason) falls back."""
    from kamiwaza_sdk.exceptions import (
        BrokeredUserNotAllowlistedError,
        KamiwazaError,
        NativeRealmRequiredError,
        error_for_response,
    )

    err = error_for_response(403, {"detail": "Forbidden"}, "Generic 403")
    assert isinstance(err, KamiwazaError)
    assert not isinstance(err, BrokeredUserNotAllowlistedError)
    assert not isinstance(err, NativeRealmRequiredError)


def test_error_for_response_handles_none_body() -> None:
    """Non-JSON or empty responses produce body=None; dispatch must not
    crash and falls back to KamiwazaError."""
    from kamiwaza_sdk.exceptions import KamiwazaError, error_for_response

    err = error_for_response(500, None, "Empty response")
    assert isinstance(err, KamiwazaError)
    assert err.status_code == 500
    assert err.body is None


def test_error_for_response_handles_non_dict_body() -> None:
    """A response body that's a string or list (not a dict) falls back
    cleanly — defensive against malformed server responses."""
    from kamiwaza_sdk.exceptions import KamiwazaError, error_for_response

    err = error_for_response(500, "Internal Server Error", "Plain text body")
    assert isinstance(err, KamiwazaError)


# ---------------------------------------------------------------------------
# KamiwazaSDKError alias — design §4.2.11 names the base as KamiwazaSDKError
# ---------------------------------------------------------------------------


def test_kamiwaza_sdk_error_aliases_kamiwaza_error() -> None:
    """Per design §4.2.11 v0.3.7, KamiwazaSDKError is the canonical name in
    the import block. Existing code uses KamiwazaError; v0.3.7 adds the
    SDKError alias so both names work and ``issubclass`` round-trips."""
    from kamiwaza_sdk.exceptions import KamiwazaError, KamiwazaSDKError

    assert KamiwazaSDKError is KamiwazaError


# ---------------------------------------------------------------------------
# DeploymentFailedError — ENG-6530 async deploy_model client-side polling
# ---------------------------------------------------------------------------


def test_deployment_failed_error_carries_failure_context() -> None:
    """ENG-6530: when client-side polling observes a terminal failure
    status, the typed error carries the status plus the deployment's
    last_error_message / last_error_code so callers can react without
    re-fetching the deployment."""
    from kamiwaza_sdk.exceptions import DeploymentFailedError, KamiwazaError

    err = DeploymentFailedError(
        "deployment failed",
        status="FAILED",
        last_error_message="CUDA out of memory while loading weights",
        last_error_code="OOM",
    )
    assert isinstance(err, KamiwazaError)
    assert err.status == "FAILED"
    assert err.last_error_message == "CUDA out of memory while loading weights"
    assert err.last_error_code == "OOM"


def test_deployment_failed_error_is_a_runtime_error() -> None:
    """wait_for_deployment historically raised RuntimeError on failure
    statuses; existing ``except RuntimeError`` callers (e.g. the
    integration fixtures) must keep catching the typed replacement."""
    from kamiwaza_sdk.exceptions import DeploymentFailedError

    assert issubclass(DeploymentFailedError, RuntimeError)


def test_deployment_failed_error_context_defaults_none() -> None:
    """status / last_error_message / last_error_code / deployment_id default
    to None for failure paths where the deployment carries no metadata."""
    from kamiwaza_sdk.exceptions import DeploymentFailedError

    err = DeploymentFailedError("deployment failed")
    assert err.status is None
    assert err.last_error_message is None
    assert err.last_error_code is None
    assert err.deployment_id is None


def test_deployment_failed_error_carries_deployment_id() -> None:
    """Wait-phase failures must hand the caller the deployment id so the
    in-flight deployment can be stopped or inspected — otherwise the id is
    lost when deploy_model(wait=True) raises before returning it."""
    from kamiwaza_sdk.exceptions import DeploymentFailedError

    err = DeploymentFailedError("deployment failed", deployment_id="dep-123")
    assert err.deployment_id == "dep-123"
