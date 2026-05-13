"""Legacy ``kamiwaza.federations`` namespace — re-exports from the canonical
``kamiwaza_sdk.services.federations`` module.

WS-M3.2 / T7.5 (ENG-5039) per design v0.3.7 §4.2.11 v0.2.0→v0.3.7 namespace
evolution: the federation surface migrated into ``kamiwaza_sdk``. This module
preserves the legacy import path (``from kamiwaza.federations import
FederationsAPI``) as a thin re-export so existing M1-M3 callsites + tests
continue to work unchanged. T7.14 will layer in the full
``DeprecationWarning`` shim at the package level.

Identity invariant: ``kamiwaza.federations.X is
kamiwaza_sdk.services.federations.X`` for every name re-exported here.
"""

from __future__ import annotations

from kamiwaza_sdk.services.federations import (
    FederationProxy,
    FederationsAPI,
    FederationUsersAPI,
)

__all__ = [
    "FederationProxy",
    "FederationsAPI",
    "FederationUsersAPI",
]
