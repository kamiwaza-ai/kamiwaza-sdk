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
