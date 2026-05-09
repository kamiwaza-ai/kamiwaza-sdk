"""Typed exception hierarchy for the Kamiwaza SDK.

T5.1 ships only the base `KamiwazaError`. T5.10 layers the federation-aware
typed subclasses on top:
    - FederationPairTimeoutError (server raised 503 with psk_propagation_timeout)
    - BrokeredUserNotAllowlistedError (ext-authz 403, brokered_user_not_allowlisted)
    - MeshJobTimeoutError
    - MeshJobFailedError
    - NativeRealmRequiredError

Customer code is expected to catch the typed subclass when the failure mode
matters, or `KamiwazaError` for catch-all handling.
"""

from __future__ import annotations


class KamiwazaError(Exception):
    """Base class for all SDK-raised exceptions."""
