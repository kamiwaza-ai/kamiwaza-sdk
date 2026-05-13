"""Legacy ``kamiwaza.exceptions`` namespace — re-exports from the canonical
``kamiwaza_sdk.exceptions`` module.

WS-M3.2 / T7.2 (ENG-5036) per design v0.3.7 §4.2.11. The federation-aware
typed-exception subclasses moved to ``kamiwaza_sdk.exceptions``; this
module preserves the legacy import path (``from kamiwaza.exceptions import
KamiwazaError``) as a thin re-export so existing M1-M3 callsites + tests
continue to work unchanged. T7.14 will layer the full
``DeprecationWarning`` shim at the package level.

Identity invariant: ``kamiwaza.exceptions.X is kamiwaza_sdk.exceptions.X``
for every name re-exported here. ``isinstance()`` and ``except`` clauses
round-trip across both import paths.
"""

from __future__ import annotations

from kamiwaza_sdk.exceptions import (
    BrokeredUserNotAllowlistedError,
    FederationPairTimeoutError,
    KamiwazaError,
    MeshJobFailedError,
    MeshJobTimeoutError,
    NativeRealmRequiredError,
    error_for_response,
)

__all__ = [
    "BrokeredUserNotAllowlistedError",
    "FederationPairTimeoutError",
    "KamiwazaError",
    "MeshJobFailedError",
    "MeshJobTimeoutError",
    "NativeRealmRequiredError",
    "error_for_response",
]
