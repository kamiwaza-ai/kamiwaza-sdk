"""T7.3 / ENG-5037 — Federation-aware Pydantic models on the canonical surface.

WS-M3.2 foundation task. Migrates the M1+ federation-aware Pydantic types
from ``kamiwaza/models.py`` into ``kamiwaza_sdk/schemas/federation.py`` per
design v0.3.7 §4.2.11. The legacy ``kamiwaza/models.py`` re-exports from
this module so existing imports continue to work without code change; T7.14
adds the full DeprecationWarning shim.

All models opt into ``extra="allow"`` for forward compatibility per
``.ai/knowledge/failures/common-pitfalls.md`` — pinned-wheel customers must
not break when the server adds fields.

Models in this module support the M1+ federation API surface:

    - Federation                — cluster_federations row (M1)
    - BrokeredUser              — cluster_federation_users allowlist entry (M1)
    - JobResult                 — federated job terminal state (M1)
    - ClusterCapabilities       — capabilities-probe response (M2 / T5.19)
    - DiagnoseIssue, ClusterDiagnostics, FixOutcome, FixResult
                                — diagnose + fix surface (M2 / T5.13, T5.8)
    - GateDiscovery             — gate-discover response (M2 / T5.4)
    - ClusterOperations         — unified jobs+retrieval listing (M2 / T5.37)
    - Subject, Grant            — AuthzSubject surface (M3 / T5.5)
    - DatasetRef                — minimal Dataset shape (M3 / T5.6)
    - AttributeGateBinding      — dataset-scoped AttributeGate binding (M3 / T5.6)
    - ExecutionGateBinding      — cluster-scoped ExecutionGate binding (M3 / T5.6)
    - AttributeSchema, AttributeSchemaList
                                — declared-vocabulary surface (M3.1 / ENG-4946)

JobResult v0.3.7 change: 4 server fields previously paper-thinned via
``extra="allow"`` are now declared as typed Optional fields so customer
code gets type-checker support (``ray_job_id``, ``error_type``,
``error_message``, ``duration_seconds``).
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
    # ENG-5784 R5 H4 — the server always emits a stable cluster-identity
    # UUID on capabilities responses. Declare it explicitly so harness
    # / SDK consumers can rely on it without ``extra="allow"`` fallback
    # gymnastics. The field has been emitted since the original
    # ENG-4696 work; only the schema declaration was missing.
    local_node_id: Optional[str] = None


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

    T7.3 / v0.3.7: 4 server fields previously paper-thinned via
    ``extra="allow"`` are now declared as typed Optional fields:
      - ``ray_job_id``         — Ray's internal job identifier; useful for
        cross-referencing Ray dashboard / logs.
      - ``error_type``         — exception class name on FAILED jobs (e.g.
        ``OBOExchangeFailedError``); programmatic dispatch friend.
      - ``error_message``      — long-form structured error (stack trace +
        context); distinct from the short ``error`` summary.
      - ``duration_seconds``   — server-side wall-clock duration; populated
        on terminal states (SUCCEEDED, FAILED, CANCELED).
    """

    model_config = ConfigDict(extra="allow")

    job_id: str
    status: str
    result: Optional[Any] = None
    error: Optional[str] = None
    audit_actor: Optional[str] = None

    # T7.3 declared-from-extra fields (v0.3.7).
    ray_job_id: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    duration_seconds: Optional[float] = None


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
    # ENG-4941: server emits datetime (Pydantic serializes to ISO string
    # on the wire; pydantic coerces back on the SDK side). Matches
    # sibling BrokeredUser.created_at typing.
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DatasetRef(BaseModel):
    """T5.6 / §4.2.5 — minimal Dataset shape returned by the catalog API.

    The full Dataset (with ``schema``, ``container_urn``, ``tags``, ...)
    lives elsewhere in ``kamiwaza_sdk.schemas`` (legacy catalog surface);
    this M3 surface ships the fields setup.py needs to bind gates and
    round-trip references through the SDK.
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


class ExecutionGateBinding(BaseModel):
    """T5.6 (cluster expand) / §4.2.4 — response shape for the cluster
    execution-gate binding endpoints.

    Returned by ``kz.cluster.set_execution_gate(...)`` and
    ``kz.cluster.get_execution_gate(...)``. Cluster-scoped (one active
    binding per cluster), kind always ``"execution"``.
    """

    model_config = ConfigDict(extra="allow")

    type: str
    config: Dict[str, Any] = {}
    gate_name: str
    kind: Literal["execution"] = "execution"


class AttributeSchema(BaseModel):
    """ENG-4946 / M3.1 / §4.2.18 — declared-vocabulary attribute schema.

    Returned by ``kz.cluster.declare_attribute(...)``,
    ``kz.cluster.deprecate_attribute(...)``, ``kz.cluster.withdraw_attribute(...)``,
    and listed by ``kz.cluster.list_attributes()``. Lifecycle states:
    ``declared`` → ``deprecated`` → ``withdrawn``; only ``declared``-state
    attributes accept new values on subjects.upsert.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    """Canonical attribute name; matches KC user-profile + OIDC claim."""

    type: Literal["string", "int", "bool", "string[]"]
    """Wire-level attribute type. ``string[]`` is the multivalued shape."""

    state: Literal["declared", "deprecated", "withdrawn"]
    """Lifecycle state."""

    authority: Literal["local_admin", "self", "mesh_peer", "system"] = "local_admin"
    """Which actor may set values on subjects; defaults to local_admin."""

    sensitive: bool = False
    """If True, mapper exists but JWT claim is not issued (OQ-14)."""

    schema_version: str = "1.0"
    """Cross-cluster contract version (OQ-13); semver string."""

    declared_at: datetime
    """When the attribute was first declared in this realm."""

    deprecated_at: Optional[datetime] = None
    """Set when state transitioned declared → deprecated."""

    withdrawn_at: Optional[datetime] = None
    """Set when state transitioned to withdrawn."""

    declared_by: Optional[str] = None
    """Actor user UUID who issued the most recent declare/revive."""


class AttributeSchemaList(BaseModel):
    """ENG-4946 / M3.1 — response wrapper for ``kz.cluster.list_attributes()``.

    Wraps the vocabulary list with a top-level ``schema_version`` so
    cross-cluster compatibility checks (v1.1 work) have a place to land.
    """

    model_config = ConfigDict(extra="allow")

    attributes: List[AttributeSchema]
    schema_version: str = "v0.3.6"
