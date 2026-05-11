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

from typing import Any, List

from kamiwaza.exceptions import KamiwazaError
from kamiwaza.models import (
    ClusterCapabilities,
    ClusterDiagnostics,
    ClusterOperations,
    DiagnoseIssue,
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

        Walking-skeleton scope: jobs slice only — retrievals is an empty
        list until ``GET /api/retrieval/jobs`` (T5.30) and the SDK
        retrieval module (T5.36) ship. The contract is already the
        unified shape, so customer code written today still works when
        retrievals start populating.

        Demo bullet (2): ``kz.cluster.operations()`` lists the running
        federated job + any active retrieval.
        """
        jobs_body = self._client._request("GET", "/api/cluster/jobs/")
        return ClusterOperations(
            jobs=list(jobs_body) if isinstance(jobs_body, list) else [],
            retrievals=[],
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
