"""Contract checks for the neutral non-extension workload example."""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest

from examples.delegated_workload_client.client import (
    NeutralClientConfig,
    NeutralWorkloadClient,
    queue_message,
)
from kamiwaza_sdk.delegated_workloads import (
    AttestationProfile,
    EffectReservationRequest,
    EffectResourceRef,
    OpaqueRunQueuePayload,
    RunReservationRequest,
    RunTransition,
    RunTrigger,
    WorkloadAssertion,
)
from kamiwaza_sdk.delegated_workloads.proof import (
    AssertionAdapterRegistry,
    DPoPKeyLifecycle,
    WorkloadProof,
)

from .protocol_test_support import (
    CLAIM_ID,
    CORRELATION_ID,
    DIGEST,
    EFFECT_ID,
    GRANT_ID,
    RUN_ID,
    StubResponse,
    StubSession,
)


pytestmark = pytest.mark.contract
_ROOT = Path(__file__).parents[3]
_EXAMPLE = _ROOT / "examples/delegated_workload_client/client.py"
_BASE_URL = "https://core.example.test/api/v1/delegated-workloads"
_RUN_REFERENCE = "opaque-run-reference-0123456789abcdef"
_CAPABILITY = "header.run-capability.signature"


def test_example_has_no_extension_or_consumer_dependency() -> None:
    source = _EXAMPLE.read_text(encoding="utf-8")
    imports = _imports(ast.parse(source))

    assert not any(name.startswith("kamiwaza_extensions") for name in imports)
    assert "tomo" not in source.casefold()
    assert ".env" not in source
    assert "WorkloadProof.kubernetes" in source


@pytest.mark.parametrize(
    "base_url",
    [
        "http://core.example.test/api/v1/delegated-workloads",
        "https://core.example.test/api/v1/other",
        "https:///api/v1/delegated-workloads",
    ],
)
def test_example_rejects_insecure_or_wrong_protocol_origins(base_url: str) -> None:
    with pytest.raises(ValueError):
        NeutralClientConfig(base_url=base_url)


def test_control_plane_publishes_only_an_opaque_queue_reference() -> None:
    session = StubSession([StubResponse(201, _reservation_payload())])
    with _client(session) as client:
        reservation = client.reserve_run(
            RunReservationRequest(
                grant_id=UUID(GRANT_ID),
                revision_digest=DIGEST,
                occurrence_key="scheduled:2026-08-09T12:00:00Z",
                trigger=RunTrigger.SCHEDULED,
            )
        )

    assert queue_message(reservation) == {"run_reference": _RUN_REFERENCE}
    serialized = json.dumps(queue_message(reservation))
    assert _CAPABILITY not in serialized
    assert "workload" not in serialized
    assert "subject" not in serialized


def test_executor_claims_starts_reserves_and_finishes_under_one_fence() -> None:
    session = StubSession(
        [
            StubResponse(200, _claim_payload()),
            StubResponse(200, _transition_payload()),
            StubResponse(200, _effect_payload()),
            StubResponse(200, _transition_payload(run_status="succeeded")),
        ]
    )
    with _client(session) as client:
        claim = client.claim_run({"run_reference": _RUN_REFERENCE})
        started = claim.transition(RunTransition.START)
        effect = claim.reserve_effect(
            EffectReservationRequest(
                effect_key="document:read",
                effect_digest=DIGEST,
                action="read",
                resource=EffectResourceRef(
                    type="example.document",
                    descriptor_version="v1",
                    id="doc-7",
                ),
                audience="https://resource.example.test",
            )
        )
        finished = claim.transition(RunTransition.SUCCEED, "completed")

    assert started.run_status.value == "running"
    assert effect.effect_id == UUID(EFFECT_ID)
    assert finished.run_status.value == "succeeded"
    assert claim.safe_summary() == {
        "run_id": RUN_ID,
        "claim_id": CLAIM_ID,
        "correlation_id": CORRELATION_ID,
        "fencing_token": 3,
    }
    assert _CAPABILITY not in repr(claim)
    assert [_body(call).get("fencing_token") for call in session.calls[1::2]] == [
        3,
        3,
    ]


def test_executor_accepts_the_typed_queue_handoff() -> None:
    session = StubSession([StubResponse(200, _claim_payload())])
    message = OpaqueRunQueuePayload(run_reference=_RUN_REFERENCE)

    with _client(session) as client:
        claim = client.claim_run(message)

    assert claim.safe_summary()["run_id"] == RUN_ID


def _client(session: StubSession) -> NeutralWorkloadClient:
    return NeutralWorkloadClient(
        NeutralClientConfig(base_url=_BASE_URL),
        session,
        proof=_proof(),
    )


def _proof() -> WorkloadProof:
    registry = AssertionAdapterRegistry((_StaticAssertion(),))
    return WorkloadProof(
        DPoPKeyLifecycle.generate(),
        registry,
        AttestationProfile.KUBERNETES_OFFLINE_V1,
    )


@dataclass(frozen=True, slots=True)
class _StaticAssertion:
    profile_id: str = AttestationProfile.KUBERNETES_OFFLINE_V1.value

    def read(self) -> WorkloadAssertion:
        return WorkloadAssertion("projected-test-assertion")


def _reservation_payload() -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "status": "queued",
        "run_reference": _RUN_REFERENCE,
        "correlation_id": CORRELATION_ID,
        "authority_deadline": "2026-08-10T12:00:00Z",
    }


def _claim_payload() -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "claim_id": CLAIM_ID,
        "status": "claimed",
        "fencing_token": 3,
        "lease_expires_at": "2026-08-09T12:05:00Z",
        "run_capability": _CAPABILITY,
        "expires_at": "2026-08-09T12:05:00Z",
        "authority_deadline": "2026-08-10T12:00:00Z",
        "correlation_id": CORRELATION_ID,
    }


def _transition_payload(*, run_status: str = "running") -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "claim_id": CLAIM_ID,
        "run_status": run_status,
        "claim_status": "active" if run_status == "running" else "terminal",
        "lease_expires_at": "2026-08-09T12:05:00Z",
        "authority_deadline": "2026-08-10T12:00:00Z",
        "correlation_id": CORRELATION_ID,
    }


def _effect_payload() -> dict[str, object]:
    return {
        "effect_id": EFFECT_ID,
        "decision": "allow",
        "status": "reserved",
        "policy_version": "policy-v1",
        "reason_codes": ["allowed"],
        "effect_capability": "header.effect-capability.signature",
        "broker_handle": None,
        "valid_until": "2026-08-09T12:01:00Z",
        "correlation_id": CORRELATION_ID,
    }


def _body(call: tuple[str, str, dict[str, object]]) -> dict[str, object]:
    value = call[2]["data"]
    assert isinstance(value, bytes)
    body = json.loads(value)
    assert isinstance(body, dict)
    return body


def _imports(tree: ast.AST) -> set[str]:
    direct = {
        name.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for name in node.names
    }
    relative = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    return direct | relative
