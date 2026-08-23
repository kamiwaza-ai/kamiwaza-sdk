"""Safe recorded protocol fixtures for delegated-workload SDK contracts."""

from __future__ import annotations

from collections.abc import Mapping

CORRELATION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
RUN_ID = "11111111-1111-4111-8111-111111111111"
CLAIM_ID = "22222222-2222-4222-8222-222222222222"
EFFECT_ID = "33333333-3333-4333-8333-333333333333"
GRANT_ID = "44444444-4444-4444-8444-444444444444"
TENANT_ID = "55555555-5555-4555-8555-555555555555"
SUBJECT_ID = "66666666-6666-4666-8666-666666666666"
CLIENT_ID = "77777777-7777-4777-8777-777777777777"
ROLE_ID = "88888888-8888-4888-8888-888888888888"
INSTANCE_ID = "99999999-9999-4999-8999-999999999999"
WORKLOAD_REVISION_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
RESOURCE_REVISION_ID = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
ENVELOPE_ID = "cccccccc-dddd-4eee-8fff-000000000000"
DIGEST = "sha256:" + "d" * 64
RESUME_REFERENCE = "opaque-resume-reference-0123456789abcdef"


class StubResponse:
    """Minimal requests-compatible response with recorded JSON."""

    def __init__(
        self,
        status_code: int,
        body: object,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._body = body
        self.headers = dict(headers or {})

    def json(self) -> object:
        return self._body


class StubSession:
    """Record protocol calls and return responses in order."""

    def __init__(self, responses: list[StubResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method: str, url: str, **kwargs: object) -> StubResponse:
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def run_detail_payload() -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "claim_id": CLAIM_ID,
        "run_status": "running",
        "claim_status": "active",
        "lease_expires_at": "2026-08-09T12:05:00Z",
        "authority_deadline": "2026-08-10T12:00:00Z",
        "correlation_id": CORRELATION_ID,
        "grant_id": GRANT_ID,
        "occurrence_key": "scheduled:2026-08-09T12:00:00Z",
        "revision_digest": DIGEST,
        "updated_at": "2026-08-09T12:00:01Z",
    }


def effect_detail_payload(*, resume: bool = True) -> dict[str, object]:
    return {
        "effect_id": EFFECT_ID,
        "decision": "pending_approval" if resume else "allow",
        "status": "pending_approval" if resume else "reserved",
        "policy_version": "policy-v1",
        "reason_codes": ["approval_required"] if resume else ["allowed"],
        "effect_capability": None,
        "broker_handle": None,
        "valid_until": "2026-08-09T12:01:00Z",
        "correlation_id": CORRELATION_ID,
        "run_id": RUN_ID,
        "effect_key": "document:mutate",
        "request_digest": DIGEST,
        "lifecycle_status": "reserved",
        "consumed_at": None,
        "approval_id": None,
        "resume_run_reference": RESUME_REFERENCE if resume else None,
        "updated_at": "2026-08-09T12:00:02Z",
    }


def requester_context_payload() -> dict[str, object]:
    return {
        "tenant_id": TENANT_ID,
        "subject_id": SUBJECT_ID,
        "client_id": CLIENT_ID,
        "workload_role_id": ROLE_ID,
        "workload_instance_id": INSTANCE_ID,
        "workload_revision_id": WORKLOAD_REVISION_ID,
        "resource_registration_revision_id": RESOURCE_REVISION_ID,
        "run_claim_id": CLAIM_ID,
        "activation_epoch": 7,
        "grant_id": GRANT_ID,
        "run_id": RUN_ID,
        "effect_id": EFFECT_ID,
        "action": "mutate",
        "resource": {
            "type": "example.document",
            "descriptor_version": "v1",
            "id": "doc-7",
        },
        "audience": "https://resource.example.test",
        "policy_version": "policy-v1",
        "fencing_token": 3,
        "authority_envelope_id": ENVELOPE_ID,
        "correlation_id": CORRELATION_ID,
    }


def authorization_payload() -> dict[str, object]:
    return {
        "effect_id": EFFECT_ID,
        "decision": "allow",
        "reason_codes": ["allowed"],
        "requester_context": requester_context_payload(),
        "consumption_token": "one-use-consumption-token",
        "correlation_id": CORRELATION_ID,
    }


def consumption_payload() -> dict[str, object]:
    return {
        "effect_id": EFFECT_ID,
        "status": "executing",
        "requester_context": requester_context_payload(),
        "correlation_id": CORRELATION_ID,
    }


def error_payload(code: str, retry: str) -> dict[str, object]:
    return {
        "error": {
            "code": code,
            "message": "safe delegated protocol failure",
            "retry_classification": retry,
            "correlation_id": CORRELATION_ID,
            "safe_details": {"source": "contract-test"},
        }
    }
