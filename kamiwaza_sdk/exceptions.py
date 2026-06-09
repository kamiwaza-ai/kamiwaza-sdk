"""Typed exception hierarchy for the Kamiwaza SDK.

T7.2 / ENG-5036 (WS-M3.2, design v0.3.7 §4.2.11): the federation-aware typed
subclasses previously at ``kamiwaza/exceptions.py`` (M1+ surface) merge into
this canonical surface. The legacy `KamiwazaError` base gains optional
``status_code`` and ``body`` kwargs so a single base supports both local
errors (config / env-var problems — no HTTP context) and remote API errors
(status code + parsed response body for inspection).

Customer code is expected to catch the most-specific typed subclass when
the failure mode matters, or ``KamiwazaError`` (alias: ``KamiwazaSDKError``)
for catch-all handling.

Hierarchy:

    KamiwazaError                       (base — status_code + body kwargs)
        APIError                        (legacy HTTP-bound error class)
            VectorDBUnavailableError
        AuthenticationError             (401)
        AuthorizationError              (403 base)
            NativeRealmRequiredError    (403 + endpoint_requires_native_realm)
            BrokeredUserNotAllowlistedError (403 + brokered_user_not_allowlisted)
        NotFoundError                   (404 base)
            DatasetNotFoundError
        ValidationError                 (400/422)
        TimeoutError
        NonAPIResponseError
        TransportNotSupportedError
        DeploymentFailedError              (also RuntimeError; terminal deploy failure)
        FederationPairTimeoutError      (503 + psk_propagation_timeout)
        MeshJobTimeoutError             (mesh job exceeded wall-clock)
        MeshJobFailedError              (mesh job terminal FAILED state)

``KamiwazaSDKError`` is a name alias to ``KamiwazaError`` (preserved for
forward-compat with design v0.3.7 §4.2.11 naming).
"""

from __future__ import annotations

from typing import Any, Optional


class KamiwazaError(Exception):
    """Base exception for Kamiwaza SDK errors.

    Args:
        message: Human-readable error text.
        status_code: HTTP status code when the error came from a remote API
            response. None for local errors (config / env-var problems).
        body: Parsed response body (typically a dict) when available, for
            programmatic inspection of structured error details such as
            the ``detail.reason`` field used by federation flows.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        body: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        # Preserved for legacy callers that read err.message directly.
        self.message = message
        self.status_code = status_code
        self.body = body


# Alias: design v0.3.7 §4.2.11 names the base ``KamiwazaSDKError`` while the
# existing code uses ``KamiwazaError``. Keeping both names live preserves the
# existing imports and lets new code follow the design naming. Identical class.
KamiwazaSDKError = KamiwazaError


class APIError(KamiwazaError):
    """Legacy HTTP-bound error class.

    Kept distinct from ``KamiwazaError`` because the kamiwaza_sdk service
    modules historically raise ``APIError`` specifically for HTTP failures.
    The newer ``status_code`` + ``body`` kwargs on the base let new callers
    use ``KamiwazaError`` directly; the ``response_text`` + ``response_data``
    fields below remain available for code that hasn't migrated.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_text: str | None = None,
        response_data: object | None = None,
    ):
        super().__init__(message, status_code=status_code, body=response_data)
        self.response_text = response_text
        self.response_data = response_data


class AuthenticationError(KamiwazaError):
    """Raised when authentication fails (HTTP 401)."""


class AuthorizationError(KamiwazaError):
    """Raised when the caller lacks permission for an operation (HTTP 403).

    Sub-types: ``NativeRealmRequiredError``, ``BrokeredUserNotAllowlistedError``.
    """


class NotFoundError(KamiwazaError):
    """Raised when a requested resource is not found (HTTP 404)."""


class DatasetNotFoundError(NotFoundError):
    """Raised when a catalog dataset cannot be located."""


class ValidationError(KamiwazaError):
    """Raised when input validation fails (HTTP 400 / 422)."""


class TimeoutError(KamiwazaError):
    """Raised when a request times out."""


class NonAPIResponseError(KamiwazaError):
    """Raised when the server returns a non-API response (e.g. HTML dashboard)."""

    def __init__(self, message: str | None = None):
        default_msg = "Non-API response received. Did you forget to append '/api' to your base URL?"
        super().__init__(message or default_msg)


class TransportNotSupportedError(KamiwazaError):
    """Raised when a retrieval transport cannot satisfy the request."""


class DeploymentFailedError(KamiwazaError, RuntimeError):
    """A model deployment reached a terminal failure status (ENG-6530).

    Raised by client-side deployment polling (``ServingService.
    wait_deployment_ready`` / ``deploy_model(wait=True)``) when the
    deployment enters a FAILED/ERROR/MUST_REDOWNLOAD terminal state
    (MUST_REDOWNLOAD: the server detected corrupted or incomplete model
    files; the deploy will not recover without a redownload). Also subclasses
    ``RuntimeError`` because ``wait_for_deployment`` historically raised
    ``RuntimeError`` on failure statuses — existing ``except
    RuntimeError`` callers keep working.

    Attributes:
        status: Terminal deployment status observed (e.g. ``"FAILED"``).
        last_error_message: Server-reported failure explanation, when
            the deployment carries one.
        last_error_code: Short server-side error code (e.g. ``"OOM"``,
            ``"MODEL_LOADING_FAILURE"``), when available.
        deployment_id: Id of the deployment that failed, as a string, so
            callers can stop/inspect the in-flight deployment even when
            ``deploy_model(wait=True)`` raises before returning the id.
    """

    def __init__(
        self,
        message: str,
        *,
        status: Optional[str] = None,
        last_error_message: Optional[str] = None,
        last_error_code: Optional[str] = None,
        deployment_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.last_error_message = last_error_message
        self.last_error_code = last_error_code
        self.deployment_id = deployment_id


class VectorDBUnavailableError(APIError):
    """Raised when the VectorDB service reports no backend is configured."""


# ----------------------------------------------------------------------------
# Federation-aware typed subclasses (T7.2 / ENG-5036 — migrated from kamiwaza/)
#
# Each subclass corresponds to a specific server-provided ``detail.reason``
# shape so customer code can pattern-match on the failure mode instead of
# inspecting the body manually. Unrecognized reasons fall back to the base
# ``KamiwazaError`` — typed subclasses are NOT fabricated for shapes we
# don't own.
# ----------------------------------------------------------------------------


class FederationPairTimeoutError(KamiwazaError):
    """503 with ``detail.reason == "psk_propagation_timeout"`` after the SDK
    retry middleware exhausts its 90s wall-clock budget. Server-side
    correlate: receiver-side pair barrier (kamiwaza/cluster/exceptions.py)."""


class NativeRealmRequiredError(AuthorizationError):
    """403 with ``detail.reason == "endpoint_requires_native_realm"`` — the
    request was made via mesh proxy against a high-stakes endpoint (gate
    binding, federation pair, federation user revoke, federation disconnect)
    that the native-realm guard refuses on principle. The operation must be
    performed locally on the receiver, not via the federation API."""


class BrokeredUserNotAllowlistedError(AuthorizationError):
    """403 with ``detail.reason == "brokered_user_not_allowlisted"`` —
    ext-authz refuses a brokered user whose external_id isn't on the
    receiver's allowlist. Customer code typically calls
    ``kz.federations[name].users.add(...)`` to onboard then retries."""


class MeshJobTimeoutError(KamiwazaError):
    """A federated job exceeded its wall-clock budget while the SDK was
    waiting on completion. Surfaces as the typed class so customers can
    distinguish "cluster too slow" from "cluster broken"."""


class MeshJobFailedError(KamiwazaError):
    """A federated job ran but produced a non-success terminal state
    (``status == "FAILED"`` or similar). Body carries the receiver-side
    error context for diagnostics."""


# ----------------------------------------------------------------------------
# Gate-package typed subclasses (T7.13 / ENG-4767 — WS-M5)
#
# Map server-emitted ``detail.reason`` shapes to SDK-side typed errors so
# customer code can pattern-match on the gate-package failure mode instead
# of inspecting the body. Server side raises FastAPI ``HTTPException(
# status_code=N, detail={"reason": "X", ...})`` directly from the gate-
# packages API handler (T7.2).
# ----------------------------------------------------------------------------


class GatePackageHashRequiredError(ValidationError):
    """400 with ``detail.reason == "hash_required"`` — POST/PUT to
    ``/api/authz/gate-packages`` without ``hash_digest`` in the request body.
    Hash-pinning is mandatory at MVP per FR-89/FR-89a; customer code must
    pin a known SHA-256 of the wheel."""


class GatePackageHashMismatchError(KamiwazaError):
    """422 with ``detail.reason == "hash_mismatch"`` — pip ``--require-hashes``
    rejected the install because the wheel served from the index doesn't
    match the supplied ``hash_digest``. Either the wheel was retagged on the
    index or the customer pinned the wrong hash. Body carries the expected
    vs observed digests."""


class GatePackageInstallTimeoutError(KamiwazaError):
    """504 with ``detail.reason == "install_timeout"`` — pip-install
    subprocess exceeded the chart-configured ``authz.gatePackages.installTimeoutSeconds``
    (default 120s). Compiled dependencies on slow hosts may need a higher
    timeout. Body carries the configured limit + actual elapsed."""


class GatePackageClasspathDropError(KamiwazaError):
    """409 with ``detail.reason == "classpath_drop"`` — PUT-replace refused
    because the new package's classpath set is not a superset of the
    currently-bound classpaths. Customer must explicitly unbind the dropped
    classpath first. Body carries the dropped classpath list."""


class GatePackageUninstallBlockedError(KamiwazaError):
    """409 with ``detail.reason == "uninstall_blocked"`` — DELETE refused
    because one or more active bindings (``Cluster.executionGate`` or
    ``Dataset.gate``) reference a classpath from the package. Customer must
    unbind first. Body carries the blocking bindings."""


class GatePackageNotFoundError(NotFoundError):
    """404 with ``detail.reason == "gate_package_not_found"`` — GET/PUT/DELETE
    on a name that doesn't exist in ``cluster_gate_packages``. Customer may
    have a stale package name reference or a typo."""


# ----------------------------------------------------------------------------
# Response → typed-exception dispatch table (T7.2 — migrated from kamiwaza/)
#
# The status code is part of the match alongside the reason string to avoid
# type confusion (a 200 should never be cast to FederationPairTimeoutError
# even if its body pathologically contains the reason string).
# ----------------------------------------------------------------------------


_REASON_TO_EXCEPTION: dict[tuple[int, str], type[KamiwazaError]] = {
    (503, "psk_propagation_timeout"): FederationPairTimeoutError,
    (403, "brokered_user_not_allowlisted"): BrokeredUserNotAllowlistedError,
    (403, "endpoint_requires_native_realm"): NativeRealmRequiredError,
    # T7.13 / ENG-4767 — gate-package typed errors (WS-M5)
    (400, "hash_required"): GatePackageHashRequiredError,
    (422, "hash_mismatch"): GatePackageHashMismatchError,
    (504, "install_timeout"): GatePackageInstallTimeoutError,
    (409, "classpath_drop"): GatePackageClasspathDropError,
    (409, "uninstall_blocked"): GatePackageUninstallBlockedError,
    (404, "gate_package_not_found"): GatePackageNotFoundError,
}


def error_for_response(status_code: int, body: Any, message: str) -> KamiwazaError:
    """Construct the most-specific KamiwazaError subclass for a response.

    Inspects the parsed response body for ``detail.reason`` and falls back
    to the base ``KamiwazaError`` for shapes the dispatch table doesn't
    know. Used at the response → exception boundary in the unified
    ``KamiwazaClient`` (T7.4 ports the retry middleware to use this).

    Args:
        status_code: HTTP status from the response.
        body: Parsed JSON body (typically a dict) or None when parsing
            failed / response was non-JSON. Defensive against str / list
            bodies — they fall through to the base class.
        message: Human-readable error text composed by the caller.

    Returns:
        An instance of KamiwazaError or one of its registered subclasses.
    """
    reason: Optional[str] = None
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, dict):
            reason_value = detail.get("reason")
            if isinstance(reason_value, str):
                reason = reason_value

    cls: type[KamiwazaError] = KamiwazaError
    if reason is not None:
        cls = _REASON_TO_EXCEPTION.get((status_code, reason), KamiwazaError)

    return cls(message, status_code=status_code, body=body)
