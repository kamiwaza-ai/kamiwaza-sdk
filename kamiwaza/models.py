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


class FixOutcome(BaseModel):
    """T5.8 / ENG-4693 — per-issue outcome from kz.cluster.fix().

    ``status`` is one of:
      - ``fixed``           — issue.fix_endpoint returned 2xx.
      - ``manual_required`` — issue.auto_fixable was False; skipped.
      - ``failed``          — fix_endpoint returned non-2xx; ``error`` set.
    """

    model_config = ConfigDict(extra="allow")

    issue_id: str
    status: Literal["fixed", "manual_required", "failed"]
    error: Optional[str] = None


class FixResult(BaseModel):
    """T5.8 / ENG-4693 — aggregate result of a kz.cluster.fix() run.

    Per design `system-design.md` §4.2.10: iterates ClusterDiagnostics
    issues in severity order, dispatches each to its fix_endpoint when
    auto_fixable, records per-issue outcomes. The skeleton supports
    auto-fixable issues generically — fix() works for any future probe
    that ships with a fix_endpoint, without SDK changes.
    """

    model_config = ConfigDict(extra="allow")

    outcomes: List[FixOutcome] = []


class GateDiscovery(BaseModel):
    """T5.4 / ENG-4691 — reflection payload from POST /api/authz/gates/discover.

    Returned by ``kz.gates.discover(classpath)``. ``kind`` is one of
    ``"execution"`` or ``"attribute"``. ``required_attributes`` is the
    set of user-attribute specs the gate consumes; ``config_schema`` is
    the JSONSchema-shaped binding schema (empty dict when the gate
    doesn't declare a ``config_schema()`` classmethod).
    """

    model_config = ConfigDict(extra="allow")

    name: str
    kind: Literal["execution", "attribute"]
    required_attributes: List[Dict[str, Any]] = []
    config_schema: Dict[str, Any] = {}
    classpath: str
    location: str = ""


class ClusterOperations(BaseModel):
    """T5.37 / ENG-4714 — unified jobs+retrieval listing.

    Returned by ``kz.cluster.operations()``. The walking skeleton
    populates ``jobs`` from ``GET /api/cluster/jobs`` and leaves
    ``retrievals`` empty until ``GET /api/retrieval/jobs`` lands
    (T5.30 / ENG-4707) and the SDK retrieval module ships (T5.36).

    Demo bullet (2): list the running federated job + active retrieval.
    """

    model_config = ConfigDict(extra="allow")

    jobs: List[Any] = []
    retrievals: List[Any] = []


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


class Grant(BaseModel):
    """T5.5 / §4.2.6 — one ReBAC tuple bound to a subject.

    Returned by ``kz.subjects.grants(username).list()`` / ``.create()``.
    """

    model_config = ConfigDict(extra="allow")

    object_namespace: str
    object_id: str
    relation: str


class Subject(BaseModel):
    """T5.5 / §4.2.6 — typed Subject response.

    Returned by ``kz.subjects.upsert(...)`` and ``kz.subjects.get(...)``.
    ``attributes`` collapses single-element KC attribute lists to scalars
    on the server side so the SDK consumer reads the same shape they wrote.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    username: str
    attributes: Dict[str, Any] = {}
    grants: List[Grant] = []
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class DatasetRef(BaseModel):
    """T5.6 / §4.2.5 — minimal Dataset shape returned by the catalog API.

    The full Dataset (with ``schema``, ``container_urn``, ``tags``, ...)
    lives on the legacy ``kamiwaza_sdk`` namespace; this M3 namespace
    surfaces the fields setup.py needs to bind gates and round-trip
    references through the SDK.
    """

    model_config = ConfigDict(extra="allow")

    urn: str
    name: str
    platform: str
    environment: Optional[str] = None
    properties: Dict[str, Any] = {}


class AttributeGateBinding(BaseModel):
    """T5.6 / §4.2.5 — response shape for dataset.gate endpoints.

    Returned by ``kz.datasets.set_gate(...)`` and
    ``kz.datasets.get_gate(...)``. ``kind`` is always ``"attribute"`` on
    this surface (dataset gates are by definition AttributeGate subclasses;
    a wrong-kind PUT returns 400 before the binding is written).
    """

    model_config = ConfigDict(extra="allow")

    dataset_urn: str
    type: str
    config: Dict[str, Any] = {}
    gate_name: str
    kind: Literal["attribute"] = "attribute"
