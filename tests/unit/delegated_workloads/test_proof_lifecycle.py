"""SDK-owned workload assertion and proof lifecycle boundaries."""

from __future__ import annotations

import os
import pickle
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import jwt
import pytest

from kamiwaza_sdk.delegated_workloads import (
    AttestationProfile,
    ClaimedRun,
    DelegatedControlPlaneClient,
    DelegatedWorkloadTransport,
    DPoPKeyLifecycle,
    DPoPProof,
    DPoPProofRequest,
    ProofKeyUnavailable,
    RunReservationRequest,
    RunTrigger,
    UnsupportedAttestationProfile,
    WorkloadAssertion,
    WorkloadAssertionUnavailable,
    WorkloadProof,
)
from kamiwaza_sdk.delegated_workloads import proof as proof_module
from kamiwaza_sdk.delegated_workloads.proof import BODY_DIGEST_CLAIM

pytestmark = pytest.mark.unit
NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)
ASSERTION = "header.projected-assertion.signature"
RUN_CAPABILITY = "header.run-capability.signature"
DIGEST = "sha256:" + "d" * 64


class StubResponse:
    def __init__(self, body: object) -> None:
        self.status_code = 200
        self.headers: dict[str, str] = {}
        self._body = body

    def json(self) -> object:
        return self._body


class StubSession:
    def __init__(self, body: object) -> None:
        self._body = body
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method: str, url: str, **kwargs: object) -> StubResponse:
        self.calls.append((method, url, kwargs))
        return StubResponse(self._body)


def test_projected_assertion_adapter_reads_only_the_fixed_secure_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_path = _secure_token(tmp_path)
    monkeypatch.setattr(proof_module, "_KUBERNETES_ASSERTION_PATH", token_path)
    workload_proof = WorkloadProof.kubernetes(
        AttestationProfile.KUBERNETES_OFFLINE_V1
    )

    assertion = workload_proof.assertion()

    assert isinstance(assertion, WorkloadAssertion)
    assert assertion.get_secret_value() == ASSERTION
    assert ASSERTION not in repr(assertion)
    assert ASSERTION not in str(assertion)
    with pytest.raises(TypeError):
        WorkloadProof.kubernetes(
            AttestationProfile.KUBERNETES_OFFLINE_V1,
            token_path=tmp_path / "caller-selected-token",
        )


def test_projected_assertion_accepts_root_owned_kubelet_mode_for_group_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = os.stat_result(
        (stat.S_IFREG | 0o640, 0, 0, 1, 0, 65532, len(ASSERTION), 0, 0, 0)
    )
    monkeypatch.setattr(proof_module.os, "geteuid", lambda: 65532)
    monkeypatch.setattr(proof_module.os, "getegid", lambda: 65532)
    monkeypatch.setattr(proof_module.os, "getgroups", lambda: [65532])

    assert proof_module._secure_assertion_file(metadata)


@pytest.mark.parametrize("mode", [0o600, 0o404, 0o440 | 0o020])
def test_projected_assertion_rejects_unsafe_modes_without_leaking_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
) -> None:
    token_path = tmp_path / "token"
    token_path.write_text(ASSERTION, encoding="utf-8")
    token_path.chmod(mode)
    monkeypatch.setattr(proof_module, "_KUBERNETES_ASSERTION_PATH", token_path)

    with pytest.raises(WorkloadAssertionUnavailable) as caught:
        WorkloadProof.kubernetes(
            AttestationProfile.KUBERNETES_OFFLINE_V1
        ).assertion()

    message = str(caught.value)
    assert ASSERTION not in message
    assert str(token_path) not in message


def test_projected_assertion_rejects_empty_and_oversized_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_path = _secure_token(tmp_path, value="")
    monkeypatch.setattr(proof_module, "_KUBERNETES_ASSERTION_PATH", token_path)
    workload_proof = WorkloadProof.kubernetes(
        AttestationProfile.KUBERNETES_TOKENREVIEW_V1
    )

    with pytest.raises(WorkloadAssertionUnavailable):
        workload_proof.assertion()

    token_path.chmod(0o600)
    token_path.write_text(
        "x" * (proof_module.MAX_ASSERTION_BYTES + 1), encoding="utf-8"
    )
    token_path.chmod(0o400)
    with pytest.raises(WorkloadAssertionUnavailable):
        workload_proof.assertion()


def test_selected_profile_change_rotates_key_and_unknown_profile_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_path = _secure_token(tmp_path)
    monkeypatch.setattr(proof_module, "_KUBERNETES_ASSERTION_PATH", token_path)
    workload_proof = WorkloadProof.kubernetes(
        AttestationProfile.KUBERNETES_OFFLINE_V1
    )
    initial_thumbprint = workload_proof.key_thumbprint()

    workload_proof.select_profile(AttestationProfile.KUBERNETES_TOKENREVIEW_V1)
    rotated_thumbprint = workload_proof.key_thumbprint()
    workload_proof.select_profile(AttestationProfile.KUBERNETES_TOKENREVIEW_V1)

    assert rotated_thumbprint != initial_thumbprint
    assert workload_proof.key_thumbprint() == rotated_thumbprint
    assert workload_proof.assertion().get_secret_value() == ASSERTION
    with pytest.raises(UnsupportedAttestationProfile) as caught:
        workload_proof.select_profile("caller-defined-profile")
    assert "caller-defined-profile" not in str(caught.value)
    assert workload_proof.key_thumbprint() == rotated_thumbprint


def test_dpop_key_lifecycle_rotates_closes_and_never_serializes() -> None:
    lifecycle = DPoPKeyLifecycle.generate()
    initial_thumbprint = lifecycle.thumbprint()
    proof = lifecycle.create(_proof_request())

    assert isinstance(proof, DPoPProof)
    assert initial_thumbprint not in repr(lifecycle)
    assert proof.get_secret_value() not in repr(proof)
    assert jwt.decode(
        proof.get_secret_value(), options={"verify_signature": False}
    )[BODY_DIGEST_CLAIM] == DIGEST
    with pytest.raises(TypeError):
        pickle.dumps(lifecycle)
    with pytest.raises(TypeError):
        pickle.dumps(proof)

    lifecycle.rotate()
    assert lifecycle.thumbprint() != initial_thumbprint
    lifecycle.close()
    with pytest.raises(ProofKeyUnavailable):
        lifecycle.create(_proof_request())
    with pytest.raises(ProofKeyUnavailable):
        lifecycle.public_jwk()


def test_capability_models_and_nonce_errors_redact_serialized_values() -> None:
    claim = ClaimedRun.model_validate(_claimed_run_payload())
    authority = claim.authority(WorkloadAssertion(ASSERTION))

    assert RUN_CAPABILITY not in repr(claim)
    assert RUN_CAPABILITY not in claim.model_dump_json()
    assert RUN_CAPABILITY not in repr(authority)
    assert ASSERTION not in repr(authority)
    with pytest.raises(TypeError):
        pickle.dumps(claim.run_capability)


def test_control_plane_obtains_assertion_without_app_owned_token_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_path = _secure_token(tmp_path)
    monkeypatch.setattr(proof_module, "_KUBERNETES_ASSERTION_PATH", token_path)
    session = StubSession(_run_reservation_payload())
    identity = WorkloadProof.kubernetes(AttestationProfile.KUBERNETES_OFFLINE_V1)
    transport = DelegatedWorkloadTransport(session, proof=identity, clock=lambda: NOW)
    client = DelegatedControlPlaneClient("https://core.example.test", transport)

    client.reserve_run(
        RunReservationRequest(
            grant_id=UUID("44444444-4444-4444-8444-444444444444"),
            revision_digest=DIGEST,
            occurrence_key="scheduled:2026-08-09T12:00:00Z",
            trigger=RunTrigger.SCHEDULED,
        )
    )

    headers = session.calls[0][2]["headers"]
    assert isinstance(headers, dict)
    assert headers["X-Kamiwaza-Workload-Assertion"] == ASSERTION
    assert ASSERTION not in repr(transport)
    assert ASSERTION not in repr(identity)


def _secure_token(tmp_path: Path, *, value: str = ASSERTION) -> Path:
    token_path = tmp_path / "token"
    token_path.write_text(value, encoding="utf-8")
    token_path.chmod(0o400)
    return token_path


def _proof_request() -> DPoPProofRequest:
    return DPoPProofRequest(
        method="POST",
        target_uri="https://core.example.test/run-claims",
        access_token=RUN_CAPABILITY,
        body_digest=DIGEST,
        issued_at=NOW,
    )


def _claimed_run_payload() -> dict[str, object]:
    return {
        "run_id": "11111111-1111-4111-8111-111111111111",
        "claim_id": "22222222-2222-4222-8222-222222222222",
        "status": "claimed",
        "fencing_token": 3,
        "lease_expires_at": (NOW + timedelta(minutes=1)).isoformat(),
        "run_capability": RUN_CAPABILITY,
        "expires_at": (NOW + timedelta(minutes=1)).isoformat(),
        "authority_deadline": (NOW + timedelta(hours=12)).isoformat(),
        "correlation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    }


def _run_reservation_payload() -> dict[str, object]:
    return {
        "run_id": "11111111-1111-4111-8111-111111111111",
        "status": "queued",
        "run_reference": "r" * 32,
        "correlation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "authority_deadline": (NOW + timedelta(hours=12)).isoformat(),
    }
