"""Typed exception hierarchy for the Kamiwaza SDK.

T5.2 extends the T5.1 ``KamiwazaError`` base with a ``status_code`` attribute
so callers can branch by HTTP semantics. T5.10 layers federation-aware typed
subclasses on top:

    - FederationPairTimeoutError (server raised 503 with psk_propagation_timeout)
    - BrokeredUserNotAllowlistedError (ext-authz 403, brokered_user_not_allowlisted)
    - MeshJobTimeoutError
    - MeshJobFailedError
    - NativeRealmRequiredError

Customer code is expected to catch the typed subclass when the failure mode
matters, or ``KamiwazaError`` for catch-all handling.
"""

from __future__ import annotations

from typing import Any, Optional


class KamiwazaError(Exception):
    """Base class for all SDK-raised exceptions.

    Args:
        message: Human-readable error text.
        status_code: HTTP status code when the error came from a remote API
            response. None for local errors (e.g. config / env-var problems).
        body: Parsed response body (typically a dict) when available, for
            programmatic inspection of structured error details such as the
            ``detail.reason`` field used by the federation pair barrier.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        body: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


# T5.10 / ENG-4681 — Federation-aware typed subclasses per design §4.2.11.
#
# Each subclass corresponds to a specific server-provided
# ``detail.reason`` shape so customer code can pattern-match on the
# failure mode instead of inspecting the body manually. Unrecognized
# reasons fall back to the base ``KamiwazaError`` — we don't fabricate
# typed subclasses for shapes we don't own. Skeleton ships the five WS-M1
# scoped subclasses; T5.x cycles in WS-M2/M3 add more as new failure
# modes land.


class FederationPairTimeoutError(KamiwazaError):
    """503 with ``detail.reason == "psk_propagation_timeout"`` after the
    SDK retry middleware exhausts its 90s wall-clock budget. Server-side
    correlate: ``FederationPairTimeoutError`` raised by the receiver-side
    pair barrier (kamiwaza/cluster/exceptions.py)."""


class BrokeredUserNotAllowlistedError(KamiwazaError):
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


class NativeRealmRequiredError(KamiwazaError):
    """403 with ``detail.reason == "endpoint_requires_native_realm"`` —
    the request was made via mesh proxy against a high-stakes endpoint
    (gate binding, federation pair, federation user revoke, federation
    disconnect) that the native-realm guard refuses on principle. The
    operation must be performed locally on the receiver, not via the
    federation API."""


# Reason → exception class lookup. The status code is also part of the
# match in ``error_for_response`` to avoid type confusion (a 200 should
# never be cast to FederationPairTimeoutError even if its body
# pathologically contains the reason string).
_REASON_TO_EXCEPTION: dict[tuple[int, str], type[KamiwazaError]] = {
    (503, "psk_propagation_timeout"): FederationPairTimeoutError,
    (403, "brokered_user_not_allowlisted"): BrokeredUserNotAllowlistedError,
    (403, "endpoint_requires_native_realm"): NativeRealmRequiredError,
}


def error_for_response(status_code: int, body: Any, message: str) -> KamiwazaError:
    """Construct the most-specific KamiwazaError subclass for a response.

    Inspects the response body for ``detail.reason`` and falls back to
    the base ``KamiwazaError`` for shapes we don't own. Used by
    ``kamiwaza.client._request`` (T5.2) at the response → exception
    boundary. Kept module-local to ``exceptions`` so the dispatch table
    sits next to the class definitions and future subclasses can be
    added by editing one file.

    Args:
        status_code: HTTP status from the response.
        body: Parsed JSON body (typically a dict) or None when parsing
            failed / response was non-JSON.
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
