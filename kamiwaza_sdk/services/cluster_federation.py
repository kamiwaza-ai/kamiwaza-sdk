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
from .cluster import ClusterService

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


class ClusterAPI(ClusterService):
    """Top-level federation-aware cluster operations.

    Inherits from ``ClusterService`` (the legacy node/hardware/Ray cluster
    CRUD surface) so a single ``client.cluster`` attribute exposes both
    surfaces — legacy methods (``list_locations``, ``list_clusters``,
    ``list_nodes``, etc.) and the M3+ federation-aware methods below
    (``capabilities``, ``diagnose``, ``set_execution_gate``,
    ``declare_attribute``, etc.). No method-name collisions: the two
    surfaces are disjoint in their public APIs.
    """

    def capabilities(self) -> ClusterCapabilities:
        """Return the local cluster's capabilities (T5.19 + T5.21).

        Hits ``GET /api/cluster/cluster_capabilities``. Auth: any
        authenticated user with viewer or owner on ``cluster:<local_uuid>``.
        """
        body = self.client._request("GET", "/cluster/cluster_capabilities")
        return ClusterCapabilities.model_validate(body)

    def diagnose(self) -> ClusterDiagnostics:
        """Run cluster-health probes and return structured issues (T5.7).

        Hits ``GET /api/cluster/diagnose`` — admin-only. Each probe is
        fail-soft individually; ``has_issues`` is True iff any probe
        surfaced an error-severity issue.
        """
        body = self.client._request("GET", "/cluster/diagnose")
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
        jobs_body = self.client._request("GET", "/cluster/jobs/")
        # H3 (PR feedback): narrow the older-server fallback to 404
        # specifically. Wider catches hid 401/403/500/timeouts the same
        # as a missing endpoint and surfaced as "no retrievals" — operators
        # need to see auth + availability failures.
        try:
            retrievals_body = self.client._request("GET", "/retrieval/jobs")
        except KamiwazaError as exc:
            if exc.status_code == 404:
                retrievals_body = []
            else:
                raise
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
        response = self.client._request("PUT", "/cluster/execution-gate", json=body)
        return ExecutionGateBinding.model_validate(response)

    def get_execution_gate(self) -> ExecutionGateBinding:
        """Read the active ExecutionGate binding for this cluster."""
        response = self.client._request("GET", "/cluster/execution-gate")
        return ExecutionGateBinding.model_validate(response)

    def clear_execution_gate(self) -> None:
        """Remove this cluster's ExecutionGate binding."""
        self.client._request("DELETE", "/cluster/execution-gate")

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
            "PUT", f"/cluster/attribute-schema/{name}", json=body
        )
        return AttributeSchema.model_validate(response)

    def list_attributes(
        self, *, include_deprecated: bool = True
    ) -> List[AttributeSchema]:
        """List the realm's declared vocabulary (ENG-4946)."""
        params = {"include_deprecated": "true" if include_deprecated else "false"}
        response = self.client._request(
            "GET", "/cluster/attribute-schema", params=params
        )
        return AttributeSchemaList.model_validate(response).attributes

    def deprecate_attribute(self, name: str) -> AttributeSchema:
        """Transition an attribute from declared → deprecated (ENG-4946).

        H4 (PR feedback): the DELETE endpoint only returns
        ``{state, subjects_holding_value}`` so the SDK reads the full
        schema back via a single-name GET (rather than a full
        ``list_attributes()`` walk). Smaller race window + one fewer
        round trip. A concurrent ``withdraw_attribute`` between the
        DELETE and GET can still cause a 404; surfaced as a clear
        ``KamiwazaError`` rather than a synthesized schema. Server-side
        change to return the full schema directly from DELETE would let
        us drop the GET entirely — tracked as post-M3.2 polish per
        design v0.3.6 §4.2.18.
        """
        self.client._request("DELETE", f"/cluster/attribute-schema/{name}")
        try:
            response = self.client._request("GET", f"/cluster/attribute-schema/{name}")
        except KamiwazaError as exc:
            if exc.status_code == 404:
                raise KamiwazaError(
                    f"Attribute {name!r} was deprecated server-side but the "
                    f"subsequent GET returned 404 — concurrent withdraw "
                    f"likely. Re-fetch state with list_attributes()."
                ) from exc
            raise
        return AttributeSchema.model_validate(response)

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
            "DELETE", f"/cluster/attribute-schema/{name}", params=params
        )
        return result

    # ─── ENG-9807 — federation trust lifecycle (rotate / peer CA / reconnect) ──

    def rotate_preshared_key(self, federation_id: Any) -> Dict[str, Any]:
        """Open a pre-shared-key rotation window; the new key is returned ONCE.

        Hits ``POST /cluster/federations/{id}/rotate-preshared-key`` (ENG-9501)
        — admin, native realm only.

        Additive by design: the outgoing key keeps verifying until the window
        is closed with :meth:`complete_key_rotation`, so this call cannot sever
        the mesh and needs no acknowledgement.

        The plaintext key comes back on ``preshared_key`` and is **not
        retrievable afterwards** — the federation row stores only the secret's
        URN, exactly as the key minted at pairing does. Carry it to the peer's
        operator out of band, then close the window. A value dropped here can
        only be recovered by re-pairing.

        Returns:
            ``{federation_id, reason: "rotation_opened", rotated_at,
            preshared_key}``.

        Raises:
            KamiwazaError: 409 ``rotation_already_in_flight`` when a window is
                already open — opening a second one would overwrite the
                outgoing key and silently strand a peer still signing with the
                original; 409 ``federation_not_active`` unless the federation
                is PAIRED; 404 ``federation_not_found``.
        """
        result: Dict[str, Any] = self.client._request(
            "POST", f"/cluster/federations/{federation_id}/rotate-preshared-key"
        )
        return result

    def complete_key_rotation(
        self, federation_id: Any, *, acknowledged: bool
    ) -> Dict[str, Any]:
        """Close the rotation window, retiring the outgoing pre-shared key.

        Hits ``POST /cluster/federations/{id}/complete-key-rotation``
        (ENG-9501) — admin, native realm only.

        This is the subtractive half, and the one that can break things: a peer
        still signing with the outgoing key stops working the moment this
        returns.

        ``acknowledged`` must be True, and it is **not a check**. This cluster
        cannot observe whether the peer adopted the new key — an adopted key
        and an undelivered one are indistinguishable until traffic fails, which
        is precisely why the flag exists. It is a deliberate speed bump
        asserting the operator has delivered the value out of band, the same
        shape as ``acknowledged_fingerprint`` on :meth:`refresh_peer_ca`.

        Returns:
            ``{federation_id, reason: "rotation_closed"}``.

        Raises:
            KamiwazaError: 400 ``rotation_acknowledgement_required`` when
                ``acknowledged`` is False; 409 ``no_rotation_in_flight`` when
                no window is open; 404 ``federation_not_found``.
        """
        result: Dict[str, Any] = self.client._request(
            "POST",
            f"/cluster/federations/{federation_id}/complete-key-rotation",
            json={"acknowledged": acknowledged},
        )
        return result

    def refresh_peer_ca(
        self,
        federation_id: Any,
        *,
        ca_pem: str,
        acknowledged_fingerprint: str,
    ) -> Dict[str, Any]:
        """Replace a federation's stored peer CA after the peer rotated its own.

        Hits ``POST /cluster/federations/{id}/refresh-peer-ca`` (ENG-9507) —
        admin, native realm only. Without it the only way to replace a rotated
        peer CA is delete-and-recreate the federation.

        ``acknowledged_fingerprint`` must match the SHA-256 of the
        whitespace-normalised ``ca_pem``. That is not a security check — the
        caller supplies both halves — it is a deliberate speed bump forcing the
        operator to have LOOKED at the fingerprint, which is where the
        out-of-band comparison with the peer's operator actually happens. From
        this side a substitution and a legitimate rotation are
        indistinguishable.

        Returns:
            ``{federation_id, fingerprint, previous_fingerprint}`` — the
            fingerprints of the installed and replaced CAs.

        Raises:
            KamiwazaError: 400 ``peer_ca_required`` on an empty ``ca_pem``;
                400 ``fingerprint_acknowledgement_required`` on an empty
                acknowledgement (the refusal carries the correct
                ``fingerprint`` so the operator can verify it out of band);
                409 ``fingerprint_acknowledgement_mismatch`` when the two do
                not agree — re-verify out of band rather than retrying;
                404 ``federation_not_found``.
        """
        result: Dict[str, Any] = self.client._request(
            "POST",
            f"/cluster/federations/{federation_id}/refresh-peer-ca",
            json={
                "ca_pem": ca_pem,
                "acknowledged_fingerprint": acknowledged_fingerprint,
            },
        )
        return result

    def reconnect_federation(self, federation_id: Any) -> Dict[str, Any]:
        """Undo a disconnect this cluster performed, re-admitting the peer's guests.

        Hits ``POST /cluster/federations/{id}/reconnect`` (ENG-9694) — admin,
        native realm only, matching the revoke it reverses: re-admitting a
        peer's guests is at least as privileged as cutting them off.

        Narrow by design. It accepts a ``DISCONNECTED`` federation and nothing
        else, because it only reverses a local disconnect — where the realm,
        pre-shared key and truststore entry were all preserved. Re-pairing is
        the general flow, and it is the one that copes with a peer that rotated
        keys or tore down its realm.

        Returns:
            ``{federation_id, restored, peer_idp_enabled, guests_enabled}`` —
            ``restored`` counts the guests re-admitted at ingress; the two
            Keycloak legs are best-effort and report their own outcome.

        Raises:
            KamiwazaError: 409 ``federation_not_disconnected`` on a federation
                in any other state (the response names the status);
                404 ``federation_not_found``.
        """
        result: Dict[str, Any] = self.client._request(
            "POST", f"/cluster/federations/{federation_id}/reconnect"
        )
        return result

    def _attempt_fix(self, issue: DiagnoseIssue) -> FixOutcome:
        if not issue.auto_fixable or not issue.fix_endpoint:
            return FixOutcome(issue_id=issue.id, status="manual_required")
        # H2 (PR feedback): the server's DiagnoseIssue.fix_endpoint historically
        # carried a leading ``/api`` prefix (legacy callsites that constructed
        # full paths). Post-WS-M3.2 the client's base_url already ends in
        # ``/api``, so the legacy shape would resolve to ``/api/api/...``.
        # Strip a leading ``/api`` if present so both old + new server
        # responses route correctly. Endpoints that don't have the prefix
        # pass through unchanged.
        endpoint = issue.fix_endpoint
        if endpoint.startswith("/api/"):
            endpoint = endpoint[len("/api") :]
        try:
            self.client._request(
                "POST",
                endpoint,
                json=issue.fix_payload or {},
            )
        except KamiwazaError as exc:
            return FixOutcome(issue_id=issue.id, status="failed", error=str(exc))
        return FixOutcome(issue_id=issue.id, status="fixed")
