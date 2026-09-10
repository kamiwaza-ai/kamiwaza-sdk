"""Application-level orchestration for exact mutation governance."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from examples.delegated_resource_server.mutations import MutationOutcomeUnknown
from kamiwaza_sdk.delegated_workloads import (
    AttestationProfile,
    DelegatedExecutorClient,
    DelegatedProtocolRequest,
    DelegatedRunAuthority,
    DelegatedWorkloadTransport,
    EffectReservation,
    ProtocolRetrySafety,
    RunTransition,
    RunTransitionRequest,
    WorkloadProof,
)
from kamiwaza_sdk.delegated_workloads import proof as proof_module
from kamiwaza_sdk.delegated_workloads._protocol import json_bytes

from .exact_mutation_harness import (
    GovernedMutationPlatform,
    SafeAuditEvent,
)
from .new_resource_contract import registrar_adapter
from .new_resource_harness import (
    BASE_URL,
    CSRF_TOKEN,
    MUTATION_DIGEST,
    NOW,
    _IDS,
)
from .new_resource_support import (
    _approve,
    _claim_and_start,
    _descriptor,
    _effect_request,
    _mutation_body,
    _projected_token,
    _request,
)


@dataclass(slots=True)
class _Scenario:
    platform: GovernedMutationPlatform
    transport: DelegatedWorkloadTransport
    executor: DelegatedExecutorClient
    authority: DelegatedRunAuthority
    effect: EffectReservation


@dataclass(frozen=True, slots=True, kw_only=True)
class ExactMutationOutcome:
    success_status: int
    replay_status: int
    cancelled_status: int
    revoked_status: int
    successful_mutations: int
    cancelled_mutations: int
    revoked_mutations: int
    ambiguous_mutations: int
    ambiguous_run_status: str
    ambiguous_effect_outcome: str
    audit_events: tuple[SafeAuditEvent, ...]


def run_exact_approved_mutation_journey(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> ExactMutationOutcome:
    registration = asyncio.run(registrar_adapter().reconcile_resource(_descriptor()))
    assertion = "kubernetes-offline-v1.governed-mutation.signature"
    token_path = _projected_token(tmp_path, assertion)
    monkeypatch.setattr(proof_module, "_KUBERNETES_ASSERTION_PATH", token_path)
    scenarios = [_scenario(registration, assertion) for _ in range(4)]
    try:
        success, cancelled, revoked, ambiguous = scenarios
        return _exercise_journey(success, cancelled, revoked, ambiguous)
    finally:
        for scenario in scenarios:
            scenario.transport.close()


def _scenario(
    registration: Mapping[str, object],
    assertion: str,
) -> _Scenario:
    platform = GovernedMutationPlatform(registration, assertion)
    proof = WorkloadProof.kubernetes(AttestationProfile.KUBERNETES_OFFLINE_V1)
    transport = DelegatedWorkloadTransport(platform, proof=proof, clock=lambda: NOW)
    executor, authority = _claim_and_start(transport)
    pending = executor.reserve_effect(
        _effect_request("mutate", MUTATION_DIGEST),
        authority,
    )
    _approve(platform, pending)
    effect = executor.reserve_effect(
        _effect_request("mutate", MUTATION_DIGEST),
        authority,
    )
    return _Scenario(platform, transport, executor, authority, effect)


def _exercise_journey(
    success: _Scenario,
    cancelled: _Scenario,
    revoked: _Scenario,
    ambiguous: _Scenario,
) -> ExactMutationOutcome:
    success_status = _mutate(success)
    replay_status = _mutate(success)
    _cancel(cancelled)
    cancelled_status = _mutate(cancelled)
    _revoke(revoked)
    revoked_status = _mutate(revoked)
    ambiguous.platform.mutations.lose_next_response()
    _require_unknown_outcome(ambiguous)
    terminal = ambiguous.executor.transition(
        RunTransitionRequest(
            transition=RunTransition.AMBIGUOUS,
            outcome_category="external_response_lost",
        ),
        ambiguous.authority,
    )
    return _outcome(
        (success, cancelled, revoked, ambiguous),
        (success_status, replay_status, cancelled_status, revoked_status),
        terminal.run_status.value,
    )


def _mutate(scenario: _Scenario) -> int:
    request = _request(
        scenario.transport,
        scenario.effect,
        "PUT",
        _mutation_body(),
    )
    return scenario.transport.send(request).status_code


def _cancel(scenario: _Scenario) -> None:
    request = DelegatedProtocolRequest(
        method="POST",
        url=BASE_URL + f"/runs/{scenario.authority.run_id}/cancellation",
        body=json_bytes({"reason_code": "member_requested"}),
        extra_headers=((
            "X-Kamiwaza-Workload-Assertion",
            scenario.transport.workload_assertion(),
        ),),
        retry_safety=ProtocolRetrySafety.IDEMPOTENT_PROTOCOL,
    )
    scenario.transport.send_json(request)


def _revoke(scenario: _Scenario) -> None:
    response = scenario.platform.request(
        "DELETE",
        BASE_URL + f"/grants/{_IDS['grant_id']}",
        data=json_bytes({"expected_version": 1, "reason": "member_requested"}),
        headers={"X-CSRF-Token": CSRF_TOKEN},
    )
    if response.status_code != 204:
        raise AssertionError("neutral grant revocation failed")


def _require_unknown_outcome(scenario: _Scenario) -> None:
    try:
        _mutate(scenario)
    except MutationOutcomeUnknown:
        return
    raise AssertionError("neutral lost response was not ambiguous")


def _outcome(
    scenarios: tuple[_Scenario, _Scenario, _Scenario, _Scenario],
    statuses: tuple[int, int, int, int],
    run_status: str,
) -> ExactMutationOutcome:
    success, cancelled, revoked, ambiguous = scenarios
    ambiguous_record = ambiguous.platform.mutations.records[0]
    audits = tuple(
        event
        for scenario in scenarios
        for event in scenario.platform.audit_events
    )
    return ExactMutationOutcome(
        success_status=statuses[0],
        replay_status=statuses[1],
        cancelled_status=statuses[2],
        revoked_status=statuses[3],
        successful_mutations=len(success.platform.mutations.records),
        cancelled_mutations=len(cancelled.platform.mutations.records),
        revoked_mutations=len(revoked.platform.mutations.records),
        ambiguous_mutations=len(ambiguous.platform.mutations.records),
        ambiguous_run_status=run_status,
        ambiguous_effect_outcome=ambiguous_record.outcome,
        audit_events=audits,
    )


__all__ = ("ExactMutationOutcome", "run_exact_approved_mutation_journey")
