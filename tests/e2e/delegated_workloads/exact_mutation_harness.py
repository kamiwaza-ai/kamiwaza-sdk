"""Governance and safe-audit extensions for the neutral resource harness."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from examples.delegated_resource_server.mutations import MutationOutcomeUnknown

from .new_resource_harness import (
    BASE_URL,
    CSRF_TOKEN,
    EffectRecord,
    NeutralResourcePlatform,
    RequestView,
    StubResponse,
    _CORRELATION_ID,
    _IDS,
    _effect_by_id,
    _require_guard_headers,
    _transition_payload,
)


@dataclass(frozen=True, slots=True)
class SafeAuditEvent:
    action: str
    correlation_id: str
    subject_id: str
    workload_instance_id: str
    run_id: str
    effect_id: UUID | None


class GovernedMutationPlatform(NeutralResourcePlatform):
    """Add deterministic cancellation, revocation, ambiguity, and audit."""

    def __init__(
        self,
        registration: Mapping[str, object],
        assertion: str,
    ) -> None:
        super().__init__(registration, assertion)
        self.audit_events: list[SafeAuditEvent] = []
        self._boundary: Literal["cancelled", "revoked"] | None = None

    def _core_response(self, view: RequestView) -> StubResponse:
        path = view.url.removeprefix(BASE_URL)
        if view.method == "DELETE" and path.startswith("/grants/"):
            return self._revoke_grant(view)
        return super()._core_response(view)

    def _workload_response(self, path: str, view: RequestView) -> StubResponse:
        if path.endswith("/cancellation"):
            self._boundary = "cancelled"
            self.audit_events.append(_event("run.cancellation_requested"))
            return StubResponse(
                200,
                _transition_payload("cancel_requested", "active"),
            )
        return super()._workload_response(path, view)

    def _approval_response(self, path: str, view: RequestView) -> StubResponse:
        response = super()._approval_response(path, view)
        if path != "/effects/pending-approval":
            self.audit_events.append(
                _event("approval.approved", self.effects["mutate"])
            )
        return response

    def _authorize_effect(self, view: RequestView) -> StubResponse:
        record = _effect_by_id(self.effects, view.payload.get("effect_id"))
        if self._boundary is not None:
            _require_guard_headers(view, self.assertion)
            action = f"effect.{self._boundary}_authority_denied"
            self.audit_events.append(_event(action, record))
            return StubResponse(200, _denial(record))
        response = super()._authorize_effect(view)
        if record.consumed:
            self.audit_events.append(_event("effect.replay_denied", record))
        return response

    def _consume_effect(self, view: RequestView) -> StubResponse:
        response = super()._consume_effect(view)
        record = _effect_by_id(self.effects, _effect_id(view.url))
        self.audit_events.append(_event("effect.consumed", record))
        return response

    def _resource_response(self, view: RequestView) -> StubResponse:
        try:
            response = super()._resource_response(view)
        except MutationOutcomeUnknown:
            self.audit_events.append(
                _event("effect.ambiguous", self.effects["mutate"])
            )
            raise
        if response.status_code == 200 and view.method == "PUT":
            self.audit_events.append(
                _event("effect.succeeded", self.effects["mutate"])
            )
        return response

    def _transition(self, view: RequestView) -> StubResponse:
        if view.payload.get("transition") == "ambiguous":
            self.audit_events.append(_event("run.ambiguous"))
            return StubResponse(200, _transition_payload("ambiguous", "terminal"))
        return super()._transition(view)

    def _revoke_grant(self, view: RequestView) -> StubResponse:
        if view.headers.get("X-CSRF-Token") != CSRF_TOKEN:
            return StubResponse(403, {"error": {"code": "csrf_rejected"}})
        self._boundary = "revoked"
        self.audit_events.append(_event("grant.revoked"))
        return StubResponse(204, {})


def _event(
    action: str,
    effect: EffectRecord | None = None,
) -> SafeAuditEvent:
    return SafeAuditEvent(
        action=action,
        correlation_id=_CORRELATION_ID,
        subject_id=_IDS["subject_id"],
        workload_instance_id=_IDS["instance_id"],
        run_id=_IDS["run_id"],
        effect_id=effect.effect_id if effect is not None else None,
    )


def _denial(record: EffectRecord) -> dict[str, object]:
    return {
        "effect_id": str(record.effect_id),
        "decision": "deny",
        "reason_codes": ["current_authority_denied"],
        "requester_context": None,
        "consumption_token": None,
        "correlation_id": _CORRELATION_ID,
    }


def _effect_id(url: str) -> str:
    return url.split("/effects/", 1)[1].split("/", 1)[0]


__all__ = ("GovernedMutationPlatform", "SafeAuditEvent")
