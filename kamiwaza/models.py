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

# Explicit ``X as X`` re-exports per mypy strict mode's
# ``no_implicit_reexport``. The ``__all__`` below also lists them, but
# strict mode requires the explicit-alias form on the import side to count
# each name as a re-export of this module.
from kamiwaza_sdk.schemas.federation import (
    AttributeGateBinding as AttributeGateBinding,
)
from kamiwaza_sdk.schemas.federation import (
    AttributeSchema as AttributeSchema,
)
from kamiwaza_sdk.schemas.federation import (
    AttributeSchemaList as AttributeSchemaList,
)
from kamiwaza_sdk.schemas.federation import (
    BrokeredUser as BrokeredUser,
)
from kamiwaza_sdk.schemas.federation import (
    ClusterCapabilities as ClusterCapabilities,
)
from kamiwaza_sdk.schemas.federation import (
    ClusterDiagnostics as ClusterDiagnostics,
)
from kamiwaza_sdk.schemas.federation import (
    ClusterOperations as ClusterOperations,
)
from kamiwaza_sdk.schemas.federation import (
    DatasetRef as DatasetRef,
)
from kamiwaza_sdk.schemas.federation import (
    DiagnoseIssue as DiagnoseIssue,
)
from kamiwaza_sdk.schemas.federation import (
    ExecutionGateBinding as ExecutionGateBinding,
)
from kamiwaza_sdk.schemas.federation import (
    Federation as Federation,
)
from kamiwaza_sdk.schemas.federation import (
    FixOutcome as FixOutcome,
)
from kamiwaza_sdk.schemas.federation import (
    FixResult as FixResult,
)
from kamiwaza_sdk.schemas.federation import (
    GateDiscovery as GateDiscovery,
)
from kamiwaza_sdk.schemas.federation import (
    Grant as Grant,
)
from kamiwaza_sdk.schemas.federation import (
    JobResult as JobResult,
)
from kamiwaza_sdk.schemas.federation import (
    Subject as Subject,
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
