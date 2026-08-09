"""Application-level orchestration for neutral resource onboarding."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from kamiwaza_sdk.delegated_workloads import (
    ApprovalDecision,
    ApprovalDecisionRequest,
    AttestationProfile,
    DelegatedApprovalAPI,
    DelegatedCapability,
    DelegatedControlPlaneClient,
    DelegatedExecutorClient,
    DelegatedProtocolRequest,
    DelegatedRunAuthority,
    DelegatedWorkloadTransport,
    EffectReservation,
    EffectReservationRequest,
    EffectResourceRef,
    ProtocolRetrySafety,
    RunReservationRequest,
    RunTransition,
    RunTransitionRequest,
    RunTrigger,
    WorkloadProof,
)
from kamiwaza_sdk.delegated_workloads import proof as proof_module
from kamiwaza_sdk.delegated_workloads._protocol import json_bytes

from .new_resource_contract import registrar_adapter
from .new_resource_harness import (
    BASE_URL,
    CSRF_TOKEN,
    DOCUMENT_URL,
    MEMBER_SUBJECT_ID,
    MUTATION_DIGEST,
    NOW,
    POLICY_VERSION,
    READ_DIGEST,
    RESOURCE_AUDIENCE,
    NeutralResourcePlatform,
)

EXPECTED_STEPS = (
    "activate_resource",
    "reserve_run",
    "claim_run",
    "start_run",
    "reserve_mutation_pending",
    "list_pending_approval",
    "approve_mutation",
    "reserve_mutation_approved",
    "authorize_mutate",
    "consume_mutate",
    "mutate_document",
    "reserve_read",
    "authorize_read",
    "consume_read",
    "read_document",
    "deny_replay",
    "finish_run",
)


@dataclass(frozen=True, slots=True, kw_only=True)
class NewResourceOutcome:
    steps: tuple[str, ...]
    registration_status: str
    pending_mutation_had_no_capability: bool
    approved_effect_id: UUID
    mutation_effect_id: UUID
    document_title: str
    document_version: int
    member_subject_id: str
    workload_actor_id: str
    guard_consumptions: int
    replay_status: int


def run_new_resource_onboarding_journey(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> NewResourceOutcome:
    descriptor = _descriptor()
    registration = asyncio.run(registrar_adapter().reconcile_resource(descriptor))
    assertion = "kubernetes-offline-v1.neutral-resource-assertion.signature"
    token_path = _projected_token(tmp_path, assertion)
    monkeypatch.setattr(proof_module, "_KUBERNETES_ASSERTION_PATH", token_path)
    platform = NeutralResourcePlatform(registration, assertion)
    platform.steps.append("activate_resource")
    proof = WorkloadProof.kubernetes(AttestationProfile.KUBERNETES_OFFLINE_V1)
    transport = DelegatedWorkloadTransport(platform, proof=proof, clock=lambda: NOW)
    try:
        return _run_journey(platform, transport, registration)
    finally:
        transport.close()


def _run_journey(
    platform: NeutralResourcePlatform,
    transport: DelegatedWorkloadTransport,
    registration: Mapping[str, object],
) -> NewResourceOutcome:
    executor, authority = _claim_and_start(transport)
    pending_request = _effect_request("mutate", MUTATION_DIGEST)
    pending = executor.reserve_effect(pending_request, authority)
    approved_effect = _approve(platform, pending)
    mutation = executor.reserve_effect(pending_request, authority)
    mutated = _invoke(transport, mutation, "PUT", _mutation_body())
    read = executor.reserve_effect(_effect_request("read", READ_DIGEST), authority)
    document = _invoke(transport, read, "GET", b"")
    replay = _send(transport, mutation, "PUT", _mutation_body()).status_code
    executor.transition(
        RunTransitionRequest(transition=RunTransition.SUCCEED), authority
    )
    if platform.approved_effect_id is None:
        raise AssertionError("neutral mutation approval was not recorded")
    return NewResourceOutcome(
        steps=tuple(platform.steps),
        registration_status=str(registration["status"]),
        pending_mutation_had_no_capability=pending.effect_capability is None,
        approved_effect_id=approved_effect,
        mutation_effect_id=mutation.effect_id,
        document_title=str(document["title"]),
        document_version=_integer(document["version"]),
        member_subject_id=str(mutated["subject_id"]),
        workload_actor_id=str(mutated["actor_id"]),
        guard_consumptions=platform.guard_consumptions,
        replay_status=replay,
    )


def _claim_and_start(
    transport: DelegatedWorkloadTransport,
) -> tuple[DelegatedExecutorClient, DelegatedRunAuthority]:
    reservation = DelegatedControlPlaneClient(BASE_URL, transport).reserve_run(
        RunReservationRequest(
            grant_id=UUID("44444444-4444-4444-8444-444444444444"),
            revision_digest="sha256:" + "a" * 64,
            occurrence_key="neutral-new-resource",
            trigger=RunTrigger.TEST,
        )
    )
    executor = DelegatedExecutorClient(BASE_URL, transport)
    claim = executor.claim_run(reservation.queue_payload())
    authority = executor.authority(claim)
    executor.transition(
        RunTransitionRequest(transition=RunTransition.START), authority
    )
    return executor, authority


def _approve(platform: NeutralResourcePlatform, pending: EffectReservation) -> UUID:
    approvals = DelegatedApprovalAPI(BASE_URL, platform)
    listed = approvals.list_pending(
        UUID("11111111-1111-4111-8111-111111111111")
    )
    assert tuple(item.effect_id for item in listed) == (pending.effect_id,)
    detail = approvals.decide(
        ApprovalDecisionRequest(
            effect_id=pending.effect_id,
            effect_digest=MUTATION_DIGEST,
            policy_version=POLICY_VERSION,
            decision=ApprovalDecision.APPROVE,
            csrf_token=CSRF_TOKEN,
        )
    )
    return detail.effect_id


def _effect_request(action: str, digest: str) -> EffectReservationRequest:
    return EffectReservationRequest(
        effect_key=f"document:{action}",
        effect_digest=digest,
        action=action,
        resource=EffectResourceRef(
            type="conformance.document",
            descriptor_version="v1",
            id="doc-7",
        ),
        audience=RESOURCE_AUDIENCE,
    )


def _invoke(
    transport: DelegatedWorkloadTransport,
    reservation: EffectReservation,
    method: str,
    body: bytes,
) -> dict[str, object]:
    payload = transport.send_json(_request(transport, reservation, method, body))
    if not isinstance(payload, dict):
        raise AssertionError("neutral resource response is invalid")
    return cast(dict[str, object], payload)


def _send(
    transport: DelegatedWorkloadTransport,
    reservation: EffectReservation,
    method: str,
    body: bytes,
):
    return transport.send(_request(transport, reservation, method, body))


def _request(
    transport: DelegatedWorkloadTransport,
    reservation: EffectReservation,
    method: str,
    body: bytes,
) -> DelegatedProtocolRequest:
    capability = reservation.effect_capability
    if not isinstance(capability, DelegatedCapability):
        raise AssertionError("approved effect capability is missing")
    return DelegatedProtocolRequest(
        method=method,
        url=DOCUMENT_URL,
        body=body,
        capability=capability,
        extra_headers=((
            "X-Kamiwaza-Workload-Assertion",
            transport.workload_assertion(),
        ),),
        retry_safety=ProtocolRetrySafety.NEVER,
    )


def _mutation_body() -> bytes:
    return json_bytes({"title": "Quarterly plan"})


def _integer(value: object) -> int:
    if type(value) is not int:
        raise AssertionError("neutral resource version is invalid")
    return cast(int, value)


def _descriptor() -> dict[str, object]:
    path = Path(__file__).parents[3] / "examples/delegated_resource_server/resource-registration.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("neutral resource descriptor is invalid")
    return cast(dict[str, object], value)


def _projected_token(directory: Path, assertion: str) -> Path:
    directory.mkdir(exist_ok=True)
    token_path = directory / "neutral-resource-token"
    token_path.write_text(assertion, encoding="utf-8")
    token_path.chmod(0o400)
    return token_path


__all__ = (
    "EXPECTED_STEPS",
    "MEMBER_SUBJECT_ID",
    "NewResourceOutcome",
    "run_new_resource_onboarding_journey",
)
