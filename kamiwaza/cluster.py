"""T5.7 / T5.8 / T5.21 — kamiwaza.cluster module.

Customer-facing cluster surface per design §4.2.11 / §4.4.3 / §4.2.10:

    kz.cluster.capabilities()    -> ClusterCapabilities  (T5.21 / ENG-4698)
    kz.cluster.diagnose()        -> ClusterDiagnostics   (T5.7  / ENG-4692)
    kz.cluster.fix()             -> FixResult            (T5.8  / ENG-4693)

``capabilities()`` is mesh-routable since ENG-4697 (auth widened to
viewer); a probing peer reaches the same surface through
``kz.federations[name].probe()`` (see kamiwaza.federations.FederationProxy).

``diagnose()`` is admin-only and returns structured cluster-health issues.

``fix()`` iterates the issues and POSTs to each ``fix_endpoint`` for
auto-fixable ones. The SDK has no enumeration of probe types — dispatch
is generic on the issue shape, so future server-side probes that ship
``fix_endpoint`` + ``fix_payload`` work without SDK changes.

Server-side correlates:
- ``GET  /api/cluster/cluster_capabilities``  (extended by ENG-4696)
- ``GET  /api/cluster/diagnose``              (new in ENG-4694)
- ``POST /api/cluster/diagnose/fix/{issue_id}``  (per design §4.2.10 —
  endpoints land alongside auto-fixable probes in subsequent commits)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from kamiwaza.exceptions import KamiwazaError
from kamiwaza.models import (
    AttributeSchema,
    AttributeSchemaList,
    ClusterCapabilities,
    ClusterDiagnostics,
    ClusterOperations,
    DiagnoseIssue,
    ExecutionGateBinding,
    FixOutcome,
    FixResult,
)


_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


class ClusterAPI:
    """Top-level cluster operations on the local cluster."""

    def __init__(self, client: Any) -> None:
        # client is a kamiwaza.client.Kamiwaza instance — Any avoids a
        # runtime cycle since the client lazy-imports this module.
        self._client = client

    def capabilities(self) -> ClusterCapabilities:
        """Return the local cluster's capabilities (T5.19 + T5.21).

        Hits ``GET /api/cluster/cluster_capabilities`` on the local cluster.
        Auth: any authenticated user with viewer or owner on
        ``cluster:<local_uuid>`` (admin's install-seeded owner passes).

        Returns:
            ClusterCapabilities — hardware, available platforms, GPU count,
            federation_count, active_deployments, ray_ready, etc.
        """
        body = self._client._request("GET", "/api/cluster/cluster_capabilities")
        return ClusterCapabilities.model_validate(body)

    def diagnose(self) -> ClusterDiagnostics:
        """Run cluster-health probes and return structured issues (T5.7).

        Hits ``GET /api/cluster/diagnose`` on the local cluster — admin-only.
        Each probe is fail-soft individually; ``has_issues`` is True iff any
        probe surfaced an error-severity issue.

        Demo bullet (1) for WS-M2: clean status on a healthy cluster,
        structured issues on a partially-bootstrapped one.

        Returns:
            ClusterDiagnostics with cluster_id, timestamp, and per-issue
            DiagnoseIssue records carrying stable ``id`` strings for
            programmatic matching.
        """
        body = self._client._request("GET", "/api/cluster/diagnose")
        return ClusterDiagnostics.model_validate(body)

    def fix(self) -> FixResult:
        """Run diagnose then attempt to remediate each auto-fixable issue.

        Per design §4.2.10: iterates issues in severity order (error →
        warning → info), invokes ``issue.fix_endpoint`` with
        ``issue.fix_payload`` for each ``auto_fixable=True`` issue, and
        records per-issue outcomes. Issues with ``auto_fixable=False`` are
        surfaced as ``manual_required`` without an endpoint call. A
        fix_endpoint that returns non-2xx is recorded as ``failed`` with
        the error message — never raises, so the operator sees per-issue
        results for the whole batch.

        Returns:
            FixResult.outcomes — one FixOutcome per issue.
        """
        diagnostics = self.diagnose()
        sorted_issues = sorted(
            diagnostics.issues,
            key=lambda issue: _SEVERITY_ORDER.get(issue.severity, 99),
        )
        outcomes: List[FixOutcome] = [
            self._attempt_fix(issue) for issue in sorted_issues
        ]
        return FixResult(outcomes=outcomes)

    def operations(self) -> ClusterOperations:
        """Return a unified view of in-flight jobs and retrievals (T5.37).

        Both slices now populate from their respective listing endpoints
        (ENG-4706 for jobs, ENG-4707 for retrievals). On a missing /
        503ing retrieval listing endpoint (e.g., older server), the
        retrievals slice gracefully falls back to empty so the call
        still returns a usable shape.

        Demo bullet (2): ``kz.cluster.operations()`` lists the running
        federated job + any active retrieval.
        """
        jobs_body = self._client._request("GET", "/api/cluster/jobs/")
        try:
            retrievals_body = self._client._request("GET", "/api/retrieval/jobs")
        except KamiwazaError:
            retrievals_body = []
        return ClusterOperations(
            jobs=list(jobs_body) if isinstance(jobs_body, list) else [],
            retrievals=(
                list(retrievals_body) if isinstance(retrievals_body, list) else []
            ),
        )

    # ─── §4.2.4 — execution-gate binding (M3 expand) ──────────────────

    def set_execution_gate(
        self,
        *,
        type: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> ExecutionGateBinding:
        """Bind an ExecutionGate to this cluster (T2.4 server-side).

        Hits ``PUT /api/cluster/execution-gate``. Server validates ``type``
        is an ``ExecutionGate`` subclass and validates ``config`` against
        the gate's ``config_schema()`` before persisting.

        Args:
            type: ExecutionGate classpath, e.g.
                ``"kamiwaza.services.authz.gates.default_gates.AllowAllExecutionGate"``.
            config: Per-gate config dict. Defaults to ``{}`` for gates
                with no configurable surface.

        Returns:
            ExecutionGateBinding — the persisted shape.

        Raises:
            KamiwazaError: 400 wrong_kind when ``type`` is an
                AttributeGate; 400 schema_validation_failed when config
                violates the gate's schema.
        """
        body = {"type": type, "config": dict(config) if config else {}}
        response = self._client._request(
            "PUT", "/api/cluster/execution-gate", json=body
        )
        return ExecutionGateBinding.model_validate(response)

    def get_execution_gate(self) -> ExecutionGateBinding:
        """Read the active ExecutionGate binding for this cluster.

        Raises:
            KamiwazaError: 404 not_configured when no binding is persisted.
        """
        response = self._client._request("GET", "/api/cluster/execution-gate")
        return ExecutionGateBinding.model_validate(response)

    def clear_execution_gate(self) -> None:
        """Remove this cluster's ExecutionGate binding."""
        self._client._request("DELETE", "/api/cluster/execution-gate")

    # ─── §4.2.18 — attribute schema surface (v0.3.6 / M3.1) ──────────────

    def declare_attribute(
        self,
        name: str,
        *,
        type: str,
        sensitive: bool = False,
        authority: str = "local_admin",
        schema_version: str = "1.0",
    ) -> AttributeSchema:
        """Register an attribute in the realm's declared vocabulary (ENG-4946).

        Required BEFORE ``kz.subjects.upsert(...)`` writes for any
        attribute name not already declared. Idempotent on identical
        shape; shape change on a ``declared``-state attribute returns
        400 ``shape_change_on_declared`` — deprecate + withdraw first to
        retire the old shape.

        Hits ``PUT /api/cluster/attribute-schema/{name}``.

        Args:
            name: Canonical attribute name (e.g. ``"clearance"``).
            type: One of ``"string"``, ``"int"``, ``"bool"``, ``"string[]"``.
            sensitive: When True, the attribute is registered in the realm
                vocabulary but NOT issued as a JWT claim; gates that
                consume it must read the mesh-envelope ``user_attrs``
                field. Default False (per OQ-14 — PII attributes opt-in
                sensitive; demo flow's clearance/country/programs are not).
            authority: Which actor may set values on subjects. Default
                ``"local_admin"``.  ``"mesh_peer"`` reserves the attribute
                for cross-cluster attestation via brokered-user
                provisioning; ``"self"`` reserves for a future self-service
                surface (M3.1 declare-hook only); ``"system"`` reserves
                for platform-emitted attrs (audit/provenance).
            schema_version: Cross-cluster contract version (OQ-13); semver.

        Returns:
            AttributeSchema in ``declared`` state.

        Raises:
            KamiwazaError: 400 ``shape_change_on_declared`` if the
                attribute is already declared with a different shape.
        """
        body = {
            "type": type,
            "sensitive": sensitive,
            "authority": authority,
            "schema_version": schema_version,
        }
        response = self._client._request(
            "PUT", f"/api/cluster/attribute-schema/{name}", json=body
        )
        return AttributeSchema.model_validate(response)

    def list_attributes(
        self, *, include_deprecated: bool = True
    ) -> List[AttributeSchema]:
        """List the realm's declared vocabulary (ENG-4946).

        Hits ``GET /api/cluster/attribute-schema``. Withdrawn entries are
        tombstoned at the KC layer and never appear here; deprecated
        entries are included by default (operators need them to plan
        retirement).

        Args:
            include_deprecated: When False, omits deprecated entries.

        Returns:
            List of AttributeSchema; sorted by name.
        """
        params = {"include_deprecated": "true" if include_deprecated else "false"}
        response = self._client._request(
            "GET", "/api/cluster/attribute-schema", params=params
        )
        return AttributeSchemaList.model_validate(response).attributes

    def deprecate_attribute(self, name: str) -> AttributeSchema:
        """Transition an attribute from declared → deprecated (ENG-4946).

        Hits ``DELETE /api/cluster/attribute-schema/{name}`` (without
        force flag). Subsequent ``kz.subjects.upsert(...)`` calls that
        include this attribute reject with 400 ``attribute_deprecated``.
        Existing subject values continue to surface in JWTs until the
        attribute is withdrawn.

        Use as the first step of a planned retirement; call
        ``withdraw_attribute(...)`` once ``subjects_holding_value``
        reaches an acceptable threshold (typically 0).

        Returns:
            AttributeSchema in ``deprecated`` state.

        Raises:
            KamiwazaError: 404 ``attribute_not_found`` if absent.
        """
        response = self._client._request(
            "DELETE", f"/api/cluster/attribute-schema/{name}"
        )
        # The DELETE endpoint returns {state, subjects_holding_value}; we
        # need the full schema record, so round-trip through GET.
        # (Avoids a server-side change; M3.1 ships the lean DELETE shape.)
        _ = response
        full_list = self.list_attributes(include_deprecated=True)
        for schema in full_list:
            if schema.name == name:
                return schema
        # Defensive: server said deprecate succeeded but list omits the
        # entry. Raise the SDK's generic error rather than silently
        # synthesizing a schema with no real declared_at.
        raise KamiwazaError(
            f"Attribute {name!r} was deprecated server-side but is missing "
            "from the subsequent list_attributes() response."
        )

    def withdraw_attribute(
        self,
        name: str,
        *,
        force: bool = False,
        subjects_holding_value: int = 0,
    ) -> Dict[str, Any]:
        """Transition an attribute to withdrawn state (ENG-4946).

        Hits ``DELETE /api/cluster/attribute-schema/{name}?force=true``.
        Default refuses with 409 ``subjects_holding_value`` when subjects
        currently hold a value; ``force=True`` proceeds with explicit
        audit capturing the count + intent. Removes the KC realm
        user-profile entry + OIDC mapper. Subject KC records retain raw
        values at the KC storage layer (not auto-purged); re-declaring
        the same name later is a ``revive_from_withdrawn`` audit action.

        Args:
            name: Attribute name to withdraw.
            force: When True, proceed even when subjects hold values.
            subjects_holding_value: Caller-supplied count of subjects
                currently holding a value (operator's best estimate;
                the platform does NOT scan subjects for cost reasons).
                Default 0. force=True with non-zero count is allowed and
                audited; force=False with non-zero count returns 409.

        Returns:
            Dict with ``state`` (always ``"withdrawn"``) and
            ``subjects_holding_value`` (echoed from input for forensic
            audit-trail correlation).

        Raises:
            KamiwazaError: 404 ``attribute_not_found``; 409
                ``subjects_holding_value`` when force=False and count>0.
        """
        params: Dict[str, Any] = {
            "force": "true" if force else "false",
            "subjects_holding_value": subjects_holding_value,
        }
        return self._client._request(
            "DELETE", f"/api/cluster/attribute-schema/{name}", params=params
        )

    def _attempt_fix(self, issue: DiagnoseIssue) -> FixOutcome:
        if not issue.auto_fixable or not issue.fix_endpoint:
            return FixOutcome(issue_id=issue.id, status="manual_required")
        try:
            self._client._request(
                "POST",
                issue.fix_endpoint,
                json=issue.fix_payload or {},
            )
        except KamiwazaError as exc:
            return FixOutcome(issue_id=issue.id, status="failed", error=str(exc))
        return FixOutcome(issue_id=issue.id, status="fixed")
