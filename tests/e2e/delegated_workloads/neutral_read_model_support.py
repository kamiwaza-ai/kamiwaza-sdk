"""Shared neutral application flow for the T071 profile-parity journey."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from kamiwaza_sdk.delegated_workloads import (
    AttestationProfile,
    DelegatedCapability,
    DelegatedControlPlaneClient,
    DelegatedExecutorClient,
    DelegatedProtocolRequest,
    DelegatedRunAuthority,
    DelegatedWorkloadAPI,
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

from .neutral_read_model_harness import (
    BASE_URL,
    DIGEST,
    MEMBER_ID,
    MODEL_URL,
    RESOURCE_URL,
    WORKLOAD_ID,
    NeutralPlatformSession,
)

NOW = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
EXPECTED_APPLICATION_STEPS = (
    "reserve_run",
    "claim_run",
    "start_run",
    "read_run",
    "reserve_dataset_read",
    "read_dataset",
    "reserve_model",
    "invoke_model",
    "finish_run",
)


@dataclass(frozen=True, slots=True)
class JourneyOutcome:
    profile: AttestationProfile
    application_steps: tuple[str, ...]
    member_subject_id: str
    workload_actor_id: str
    read_result: str
    model_result: str
    attribution_is_correlated: bool


def run_neutral_read_model_journey(
    profile: AttestationProfile,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> JourneyOutcome:
    assertion = f"{profile.value}.projected-assertion.signature"
    token_path = _projected_token(tmp_path / profile.value, assertion)
    monkeypatch.setattr(proof_module, "_KUBERNETES_ASSERTION_PATH", token_path)
    session = NeutralPlatformSession(assertion)
    proof = WorkloadProof.kubernetes(profile)
    transport = DelegatedWorkloadTransport(session, proof=proof, clock=lambda: NOW)

    read_result, model_result = _run_application(transport)
    return JourneyOutcome(
        profile=profile,
        application_steps=tuple(session.application_steps),
        member_subject_id=MEMBER_ID,
        workload_actor_id=WORKLOAD_ID,
        read_result=read_result,
        model_result=model_result,
        attribution_is_correlated=_correlated(session),
    )


def _run_application(transport: DelegatedWorkloadTransport) -> tuple[str, str]:
    reservation = DelegatedControlPlaneClient(BASE_URL, transport).reserve_run(
        RunReservationRequest(
            grant_id=UUID("44444444-4444-4444-8444-444444444444"),
            revision_digest=DIGEST,
            occurrence_key="neutral-read-model",
            trigger=RunTrigger.TEST,
        )
    )
    executor = DelegatedExecutorClient(BASE_URL, transport)
    claim = executor.claim_run(reservation.queue_payload())
    authority = executor.authority(claim)
    executor.transition(RunTransitionRequest(transition=RunTransition.START), authority)
    DelegatedWorkloadAPI(BASE_URL, transport).get_run(claim.run_id)
    read = _invoke_reserved(executor, transport, authority, "read")
    model = _invoke_reserved(executor, transport, authority, "model.invoke")
    executor.transition(
        RunTransitionRequest(transition=RunTransition.SUCCEED), authority
    )
    return str(read["content"]), str(model["content"])


def _invoke_reserved(
    executor: DelegatedExecutorClient,
    transport: DelegatedWorkloadTransport,
    authority: DelegatedRunAuthority,
    action: str,
) -> dict[str, object]:
    reservation = executor.reserve_effect(
        _effect_request(action),
        authority,
    )
    capability = _effect_capability(reservation)
    method, url, body = _application_request(action)
    response = transport.send_json(
        DelegatedProtocolRequest(
            method=method,
            url=url,
            body=body,
            capability=capability,
            extra_headers=((
                "X-Kamiwaza-Workload-Assertion",
                transport.workload_assertion(),
            ),),
            retry_safety=ProtocolRetrySafety.IDEMPOTENT_PROTOCOL,
        )
    )
    assert isinstance(response, dict)
    return response


def _effect_request(action: str) -> EffectReservationRequest:
    resource_type = "example.document" if action == "read" else "platform.model"
    resource_id = "doc-7" if action == "read" else "neutral-model"
    audience = RESOURCE_URL if action == "read" else MODEL_URL
    return EffectReservationRequest(
        effect_key=f"neutral:{action}",
        effect_digest=DIGEST,
        action=action,
        resource=EffectResourceRef(
            type=resource_type,
            descriptor_version="v1",
            id=resource_id,
        ),
        audience=audience,
    )


def _application_request(action: str) -> tuple[str, str, bytes]:
    if action == "read":
        return "GET", RESOURCE_URL, b""
    return "POST", MODEL_URL, json_bytes({"model": "neutral-model"})


def _effect_capability(reservation: EffectReservation) -> DelegatedCapability:
    capability = reservation.effect_capability
    if capability is None:
        raise AssertionError("neutral effect did not return a capability")
    return capability


def _projected_token(directory: Path, assertion: str) -> Path:
    directory.mkdir()
    token_path = directory / "token"
    token_path.write_text(assertion, encoding="utf-8")
    token_path.chmod(0o400)
    return token_path


def _correlated(session: NeutralPlatformSession) -> bool:
    if len(session.attributions) != 2:
        return False
    return all(
        record.member_subject_id == record.member_charge_owner_id
        and record.workload_actor_id == record.workload_budget_subject_id
        for record in session.attributions
    )


__all__ = (
    "EXPECTED_APPLICATION_STEPS",
    "JourneyOutcome",
    "MEMBER_ID",
    "run_neutral_read_model_journey",
)
