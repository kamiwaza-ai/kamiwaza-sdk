"""T5.11 / ENG-4682 — Skeleton Pydantic models for SDK return types.

Per design §4.2.11, the SDK returns validated Pydantic models for all
WS-M1 federation flows so customer code gets type-safe access to server
responses. This module ships only the WS-M1-scoped models:

    - Federation: rows from kamiwaza.cluster_federations
    - JobResult: synchronous /run + async /submit completion shape
    - BrokeredUser: cluster_federation_users rows + provisioning state

Subsequent tickets layer additional models (Subject, Dataset,
ClusterCapabilities, Operation, Retrieval, …) — those are explicitly
scoped out of T5.11 and will follow the same pattern.

All models opt into ``extra="allow"`` for forward compatibility per
.ai/knowledge/failures/common-pitfalls.md — server-side schema evolution
must not break the SDK in a customer's pinned wheel.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict


class Federation(BaseModel):
    """Federation pairing record returned by the federations API.

    Server-side correlate: ``kamiwaza.cluster.schemas.cluster.ClusterFederation``.
    Only the fields the SDK uses today are declared explicitly; everything
    else flows through via ``extra="allow"``.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    status: str
    remote_cluster_id: Optional[str] = None
    remote_cluster_name: Optional[str] = None
    remote_ips: Optional[List[Any]] = None
    callback_hostname: Optional[str] = None


class BrokeredUser(BaseModel):
    """A federation user record — the receiver-side allowlist entry that
    governs which external_ids may be brokered into the local Keycloak
    realm. Server-side correlate: design §4.2.9c FederationUser dataclass.
    """

    model_config = ConfigDict(extra="allow")

    federation_id: str
    external_id: str
    auto_provisioned: bool = False
    created_at: Optional[datetime] = None
    initial_tuples: Optional[List[Any]] = None


class ClusterCapabilities(BaseModel):
    """T5.19 / ENG-4696 capabilities-probe response.

    Returned by ``kz.cluster.capabilities()`` (local) and
    ``kz.federations[name].probe()`` (via mesh). Server-side correlate:
    ``kamiwaza.cluster.services.ClusterService.get_cluster_capabilities()``.

    Known fields cover the WS-M2 demo-bullet-(4) probe surface — hardware /
    platform info plus federation pre-flight fields (federation_count,
    active_deployments, ray_ready). Other fields flow through via
    ``extra="allow"`` for forward compatibility per the common-pitfalls
    guide; pinned SDK wheels must not break when the server adds fields.
    """

    model_config = ConfigDict(extra="allow")

    system_type: str
    os: str
    gpu_count: int = 0
    available_platforms: List[str] = []
    federation_count: int = 0
    active_deployments: int = 0
    ray_ready: bool = False


class DiagnoseIssue(BaseModel):
    """T5.13 / ENG-4694 — a single cluster-health issue from diagnose().

    Stable ``id`` (e.g. ``admin_missing_baseline_rebac``,
    ``missing_token_exchange_permission``) lets customer code match
    programmatically. ``fix_endpoint`` + ``fix_payload`` are present when
    ``auto_fixable=True`` so a future ``fix()`` orchestration can drive
    remediation; ``None`` when manual remediation is required.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    severity: Literal["error", "warning", "info"]
    summary: str
    detail: Dict[str, Any] = {}
    fix_endpoint: Optional[str] = None
    fix_payload: Optional[Dict[str, Any]] = None
    auto_fixable: bool = False


class ClusterDiagnostics(BaseModel):
    """T5.13 / ENG-4694 — aggregate result of a cluster diagnose run.

    Returned by ``kz.cluster.diagnose()``. ``has_issues`` is True iff any
    issue has ``severity == "error"``. Server-side correlate:
    ``kamiwaza.cluster.diagnose.services.ClusterDiagnoseService.run()``.
    """

    model_config = ConfigDict(extra="allow")

    cluster_id: str
    timestamp: datetime
    issues: List[DiagnoseIssue] = []
    has_issues: bool = False


class JobResult(BaseModel):
    """Result of a federated job — synchronous /run completion or async
    /submit + poll terminal state. ``status`` is one of SUCCEEDED, FAILED,
    STOPPED, CANCELED. ``result`` is None for non-SUCCEEDED states;
    ``error`` is set on FAILED.
    """

    model_config = ConfigDict(extra="allow")

    job_id: str
    status: str
    result: Optional[Any] = None
    error: Optional[str] = None
    audit_actor: Optional[str] = None
