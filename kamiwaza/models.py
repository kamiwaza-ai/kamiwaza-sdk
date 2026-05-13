"""Legacy ``kamiwaza.models`` namespace — re-exports from the canonical
``kamiwaza_sdk.schemas.federation`` module.

WS-M3.2 / T7.3 (ENG-5037) per design v0.3.7 §4.2.11 v0.2.0→v0.3.7 namespace
evolution: the federation-aware Pydantic models migrated into the
``kamiwaza_sdk`` package. This module preserves the legacy import path
(``from kamiwaza.models import Federation``) as a thin re-export so existing
M1-M3 callsites + tests continue to work unchanged. T7.14 will layer in
the full ``DeprecationWarning`` shim at the package level.

Identity invariant: ``kamiwaza.models.X is kamiwaza_sdk.schemas.federation.X``
for every name re-exported here. ``isinstance()`` checks round-trip across
both import paths.
"""

from __future__ import annotations

from kamiwaza_sdk.schemas.federation import (
    AttributeGateBinding,
    AttributeSchema,
    AttributeSchemaList,
    BrokeredUser,
    ClusterCapabilities,
    ClusterDiagnostics,
    ClusterOperations,
    DatasetRef,
    DiagnoseIssue,
    ExecutionGateBinding,
    Federation,
    FixOutcome,
    FixResult,
    GateDiscovery,
    Grant,
    JobResult,
    Subject,
)

__all__ = [
    "AttributeGateBinding",
    "AttributeSchema",
    "AttributeSchemaList",
    "BrokeredUser",
    "ClusterCapabilities",
    "ClusterDiagnostics",
    "ClusterOperations",
    "DatasetRef",
    "DiagnoseIssue",
    "ExecutionGateBinding",
    "Federation",
    "FixOutcome",
    "FixResult",
    "GateDiscovery",
    "Grant",
    "JobResult",
    "Subject",
]
