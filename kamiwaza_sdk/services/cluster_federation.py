"""T7.7 / ENG-5041 — Federation-aware ClusterAPI on the canonical surface.

WS-M3.2 service migration. Brings the customer-facing federation cluster
surface from ``kamiwaza/cluster.py`` (M1+ + M3.1 attribute-schema) into
the canonical ``kamiwaza_sdk.services`` namespace per design v0.3.7
§4.2.11.

Module name: ``cluster_federation.py`` (not ``cluster.py``) per design §6.2
T7.7. The existing ``kamiwaza_sdk/services/cluster.py`` covers legacy
node/hardware/Ray cluster operations (Location, Hardware, Cluster, Node
CRUD); this module covers the federation-aware surface (capabilities,
diagnose, execution-gate binding, attribute-schema lifecycle). The two
do NOT overlap.

Customer-facing API (accessed via ``client.cluster``):

    kz.cluster.capabilities()    -> ClusterCapabilities  (T5.21)
    kz.cluster.diagnose()        -> ClusterDiagnostics   (T5.7)
    kz.cluster.fix()             -> FixResult            (T5.8)
    kz.cluster.operations()      -> ClusterOperations    (T5.37)
    kz.cluster.set_execution_gate(...)   / get / clear   (T2.x M3)
    kz.cluster.declare_attribute(...)    / list / dep / withdraw  (M3.1)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..exceptions import KamiwazaError
from ..schemas.federation import (
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
from .base_service import BaseService

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


class ClusterAPI(BaseService):
    """Top-level federation-aware cluster operations."""

    def capabilities(self) -> ClusterCapabilities:
        """Return the local cluster's capabilities (T5.19 + T5.21).

        Hits ``GET /api/cluster/cluster_capabilities``. Auth: any
        authenticated user with viewer or owner on ``cluster:<local_uuid>``.
        """
        body = self.client._request("GET", "/api/cluster/cluster_capabilities")
        return ClusterCapabilities.model_validate(body)

    def diagnose(self) -> ClusterDiagnostics:
        """Run cluster-health probes and return structured issues (T5.7).

        Hits ``GET /api/cluster/diagnose`` — admin-only. Each probe is
        fail-soft individually; ``has_issues`` is True iff any probe
        surfaced an error-severity issue.
        """
        body = self.client._request("GET", "/api/cluster/diagnose")
        return ClusterDiagnostics.model_validate(body)

    def fix(self) -> FixResult:
        """Run diagnose then attempt to remediate each auto-fixable issue.

        Per design §4.2.10: iterates issues in severity order, invokes
        ``issue.fix_endpoint`` with ``issue.fix_payload`` for each
        ``auto_fixable=True`` issue, records per-issue outcomes. Never
        raises — per-issue outcomes surface as ``failed`` with error str.
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

        Demo bullet (2): lists the running federated job + any active
        retrieval. Graceful fallback to empty retrievals on older servers.
        """
        jobs_body = self.client._request("GET", "/api/cluster/jobs/")
        try:
            retrievals_body = self.client._request("GET", "/api/retrieval/jobs")
        except KamiwazaError:
            retrievals_body = []
        return ClusterOperations(
            jobs=list(jobs_body) if isinstance(jobs_body, list) else [],
            retrievals=(
                list(retrievals_body) if isinstance(retrievals_body, list) else []
            ),
        )

    # ─── §4.2.4 — execution-gate binding (M3 expand) ──────────────────────

    def set_execution_gate(
        self,
        *,
        type: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> ExecutionGateBinding:
        """Bind an ExecutionGate to this cluster.

        Hits ``PUT /api/cluster/execution-gate``. Server validates ``type``
        is an ExecutionGate subclass and validates ``config`` against the
        gate's ``config_schema()`` before persisting.
        """
        body = {"type": type, "config": dict(config) if config else {}}
        response = self.client._request("PUT", "/api/cluster/execution-gate", json=body)
        return ExecutionGateBinding.model_validate(response)

    def get_execution_gate(self) -> ExecutionGateBinding:
        """Read the active ExecutionGate binding for this cluster."""
        response = self.client._request("GET", "/api/cluster/execution-gate")
        return ExecutionGateBinding.model_validate(response)

    def clear_execution_gate(self) -> None:
        """Remove this cluster's ExecutionGate binding."""
        self.client._request("DELETE", "/api/cluster/execution-gate")

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

        Required BEFORE ``kz.subjects.upsert(...)`` writes for any attribute
        name not already declared. Idempotent on identical shape; shape
        change on a declared-state attribute returns 400.
        """
        body = {
            "type": type,
            "sensitive": sensitive,
            "authority": authority,
            "schema_version": schema_version,
        }
        response = self.client._request(
            "PUT", f"/api/cluster/attribute-schema/{name}", json=body
        )
        return AttributeSchema.model_validate(response)

    def list_attributes(
        self, *, include_deprecated: bool = True
    ) -> List[AttributeSchema]:
        """List the realm's declared vocabulary (ENG-4946)."""
        params = {"include_deprecated": "true" if include_deprecated else "false"}
        response = self.client._request(
            "GET", "/api/cluster/attribute-schema", params=params
        )
        return AttributeSchemaList.model_validate(response).attributes

    def deprecate_attribute(self, name: str) -> AttributeSchema:
        """Transition an attribute from declared → deprecated (ENG-4946)."""
        response = self.client._request(
            "DELETE", f"/api/cluster/attribute-schema/{name}"
        )
        _ = response
        full_list = self.list_attributes(include_deprecated=True)
        for schema in full_list:
            if schema.name == name:
                return schema
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

        Default refuses with 409 when subjects hold values; force=True
        proceeds with explicit audit capturing the count + intent.
        """
        params: Dict[str, Any] = {
            "force": "true" if force else "false",
            "subjects_holding_value": subjects_holding_value,
        }
        result: Dict[str, Any] = self.client._request(
            "DELETE", f"/api/cluster/attribute-schema/{name}", params=params
        )
        return result

    def _attempt_fix(self, issue: DiagnoseIssue) -> FixOutcome:
        if not issue.auto_fixable or not issue.fix_endpoint:
            return FixOutcome(issue_id=issue.id, status="manual_required")
        try:
            self.client._request(
                "POST",
                issue.fix_endpoint,
                json=issue.fix_payload or {},
            )
        except KamiwazaError as exc:
            return FixOutcome(issue_id=issue.id, status="failed", error=str(exc))
        return FixOutcome(issue_id=issue.id, status="fixed")
