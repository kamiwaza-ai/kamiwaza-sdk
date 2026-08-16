"""Deterministic Core and ASGI resource boundary for the neutral E2E journey."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import UUID

import jwt

from examples.delegated_resource_server.app import (
    DocumentStore,
    ResourceApplicationConfig,
    build_application,
)
from examples.delegated_resource_server.mutations import (
    ExactApprovedMutationFixture,
)
from kamiwaza_sdk.delegated_workloads import (
    CoreResourceGuardHTTPClient,
    ProtectedResourceGuard,
)
from kamiwaza_sdk.delegated_workloads.proof import body_digest

from .new_resource_contract import RESOURCE_REVISION_ID
from .new_resource_crypto import (
    CapabilityMaterial,
    CapabilitySigner,
    proof_thumbprint,
)
from kamiwaza_sdk.delegated_workloads.proof import BODY_DIGEST_CLAIM

NOW = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
BASE_URL = "https://core.example.test/api/v1/delegated-workloads"
RESOURCE_AUDIENCE = "https://documents.example.test"
DOCUMENT_URL = RESOURCE_AUDIENCE + "/v1/documents/doc-7"
MUTATION_DIGEST = "sha256:" + "b" * 64
READ_DIGEST = "sha256:" + "c" * 64
POLICY_VERSION = "conformance-document-policy:v1"
CSRF_TOKEN = "neutral-member-csrf-token"

_EFFECT_IDS = {
    "mutate": UUID("33333333-3333-4333-8333-333333333331"),
    "read": UUID("33333333-3333-4333-8333-333333333332"),
}
_APPROVAL_ID = UUID("99999999-9999-4999-8999-999999999999")
_CORRELATION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_IDS = {
    "tenant_id": "11111111-1111-4111-8111-111111111111",
    "subject_id": "66666666-6666-4666-8666-666666666666",
    "client_id": "22222222-2222-4222-8222-222222222222",
    "revision_id": "44444444-4444-4444-8444-444444444441",
    "role_id": "44444444-4444-4444-8444-444444444442",
    "instance_id": "44444444-4444-4444-8444-444444444443",
    "grant_id": "44444444-4444-4444-8444-444444444444",
    "run_id": "55555555-5555-4555-8555-555555555551",
    "claim_id": "55555555-5555-4555-8555-555555555552",
    "envelope_id": "55555555-5555-4555-8555-555555555553",
}


@dataclass(slots=True)
class EffectRecord:
    action: str
    effect_id: UUID
    effect_digest: str
    approved: bool = False
    consumed: bool = False
    request_digest: str | None = None


@dataclass(frozen=True, slots=True)
class RequestView:
    method: str
    url: str
    body: bytes
    headers: Mapping[str, str]
    payload: Mapping[str, object]


class StubResponse:
    def __init__(self, status: int, body: object) -> None:
        self.status_code = status
        self.headers: Mapping[str, str] = {}
        self._body = body

    def json(self) -> object:
        return self._body


class NeutralResourcePlatform:
    """Exercise public SDK requests, Core guard HTTP calls, and the ASGI app."""

    def __init__(self, registration: Mapping[str, object], assertion: str) -> None:
        _require_active_registration(registration)
        self.assertion = assertion
        self.steps: list[str] = []
        self.effects: dict[str, EffectRecord] = {}
        self.approved_effect_id: UUID | None = None
        self.guard_consumptions = 0
        self._proof_thumbprint: str | None = None
        self._signer = CapabilitySigner(NOW)
        decisions = CoreResourceGuardHTTPClient(BASE_URL, self)
        guard = ProtectedResourceGuard(
            lambda _now: self._signer.jwks(), decisions, clock=lambda: NOW
        )
        config = ResourceApplicationConfig(
            audience=RESOURCE_AUDIENCE,
            registration_revision_id=RESOURCE_REVISION_ID,
        )
        store = DocumentStore()
        self.mutations = ExactApprovedMutationFixture(store)
        self._app = build_application(guard, config, store, self.mutations)

    def request(self, method: str, url: str, **kwargs: object) -> StubResponse:
        view = _request_view(method, url, kwargs)
        if url.startswith(RESOURCE_AUDIENCE):
            return self._resource_response(view)
        return self._core_response(view)

    def _core_response(self, view: RequestView) -> StubResponse:
        path = view.url.removeprefix(BASE_URL)
        if path.startswith("/effect-authorizations"):
            return self._authorize_effect(view)
        if path.endswith("/consumption"):
            return self._consume_effect(view)
        if "approval" in path or path == "/effects/pending-approval":
            return self._approval_response(path, view)
        _assert_sdk_proof(view, self.assertion)
        return self._workload_response(path, view)

    def _workload_response(self, path: str, view: RequestView) -> StubResponse:
        if path == "/runs":
            self.steps.append("reserve_run")
            return StubResponse(200, _run_reservation())
        if path == "/run-claims":
            self._capture_executor_key(view)
            self.steps.append("claim_run")
            return StubResponse(200, _claim())
        if path.endswith("/transitions"):
            return self._transition(view)
        if path.endswith("/effects"):
            return self._reserve_effect(view)
        raise AssertionError(f"unexpected neutral Core path: {path}")

    def _capture_executor_key(self, view: RequestView) -> None:
        candidate = view.payload.get("executor_proof_jwk")
        if not isinstance(candidate, Mapping):
            raise AssertionError("executor proof key is missing")
        self._proof_thumbprint = proof_thumbprint(candidate)

    def _transition(self, view: RequestView) -> StubResponse:
        transition = view.payload.get("transition")
        if transition == "start":
            self.steps.append("start_run")
            return StubResponse(200, _transition_payload("running", "active"))
        if transition == "succeed":
            self.steps.append("finish_run")
            return StubResponse(200, _transition_payload("succeeded", "terminal"))
        raise AssertionError("unexpected neutral run transition")

    def _reserve_effect(self, view: RequestView) -> StubResponse:
        action = _effect_action(view)
        record = self.effects.setdefault(action, _effect_record(action, view))
        if action == "mutate" and not record.approved:
            self.steps.append("reserve_mutation_pending")
            return StubResponse(200, _effect_reservation(record, None))
        label = "reserve_mutation_approved" if action == "mutate" else "reserve_read"
        self.steps.append(label)
        return StubResponse(200, _effect_reservation(record, self._capability(record)))

    def _capability(self, record: EffectRecord) -> str:
        if self._proof_thumbprint is None:
            raise AssertionError("executor proof key was not captured")
        return self._signer.mint(
            CapabilityMaterial(
                record.action,
                record.effect_id,
                self._proof_thumbprint,
                "document:doc-7",
                _IDS,
            )
        )

    def _approval_response(self, path: str, view: RequestView) -> StubResponse:
        record = self.effects["mutate"]
        if path == "/effects/pending-approval":
            self.steps.append("list_pending_approval")
            return StubResponse(200, [_effect_detail(record)])
        _require_exact_approval(view, record)
        record.approved = True
        self.approved_effect_id = record.effect_id
        self.steps.append("approve_mutation")
        return StubResponse(200, _effect_detail(record))

    def _authorize_effect(self, view: RequestView) -> StubResponse:
        record = _effect_by_id(self.effects, view.payload.get("effect_id"))
        _require_guard_headers(view, self.assertion)
        if record.consumed:
            self.steps.append("deny_replay")
            return StubResponse(200, _authorization(record, allow=False))
        request_digest = view.payload.get("request_digest")
        if not isinstance(request_digest, str):
            raise AssertionError("guard request digest is missing")
        record.request_digest = request_digest
        self.steps.append(f"authorize_{record.action}")
        return StubResponse(200, _authorization(record, allow=True))

    def _consume_effect(self, view: RequestView) -> StubResponse:
        record = _effect_by_id(self.effects, _effect_id_from_path(view.url))
        expected = f"consume-{record.action}"
        if view.headers.get("X-Kamiwaza-Effect-Consumption") != expected:
            raise AssertionError("one-use consumption token is invalid")
        if record.consumed:
            raise AssertionError("effect consumption was replayed")
        record.consumed = True
        self.guard_consumptions += 1
        self.steps.append(f"consume_{record.action}")
        return StubResponse(200, _consumption(record))

    def _resource_response(self, view: RequestView) -> StubResponse:
        response = asyncio.run(_call_resource(self._app, view))
        if response.status_code == 200:
            action = "mutate" if view.method == "PUT" else "read"
            self.steps.append(f"{action}_document")
        return response


def _request_view(
    method: str,
    url: str,
    kwargs: Mapping[str, object],
) -> RequestView:
    body = kwargs.get("data", b"")
    headers = kwargs.get("headers", {})
    if not isinstance(body, bytes) or not isinstance(headers, Mapping):
        raise AssertionError("neutral request shape is invalid")
    payload = json.loads(body) if body else {}
    if not isinstance(payload, Mapping):
        raise AssertionError("neutral request body is invalid")
    return RequestView(method, url, body, cast(Mapping[str, str], headers), payload)


def _assert_sdk_proof(view: RequestView, assertion: str) -> None:
    claims = jwt.decode(view.headers["DPoP"], options={"verify_signature": False})
    assert claims["htm"] == view.method
    assert claims["htu"] == view.url
    assert claims[BODY_DIGEST_CLAIM] == body_digest(view.body)
    authorization = view.headers.get("Authorization")
    if authorization is not None:
        assert claims["ath"] == _token_hash(authorization.removeprefix("DPoP "))
    if not view.url.endswith("/effects"):
        assert view.headers["X-Kamiwaza-Workload-Assertion"] == assertion


def _token_hash(value: str) -> str:
    digest = hashlib.sha256(value.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _effect_action(view: RequestView) -> str:
    action = view.payload.get("action")
    resource = view.payload.get("resource")
    checks = (
        action in ("read", "mutate"),
        isinstance(resource, Mapping),
        isinstance(resource, Mapping) and resource.get("type") == "conformance.document",
        view.payload.get("audience") == RESOURCE_AUDIENCE,
    )
    if not all(checks):
        raise AssertionError("neutral effect contract is invalid")
    return cast(str, action)


def _effect_record(action: str, view: RequestView) -> EffectRecord:
    digest = view.payload.get("effect_digest")
    if not isinstance(digest, str):
        raise AssertionError("neutral effect digest is missing")
    return EffectRecord(action, _EFFECT_IDS[action], digest)


def _effect_reservation(
    record: EffectRecord,
    capability: str | None,
) -> dict[str, object]:
    allowed = capability is not None
    return {
        "effect_id": str(record.effect_id),
        "decision": "allow" if allowed else "pending_approval",
        "status": "reserved" if allowed else "pending_approval",
        "policy_version": POLICY_VERSION,
        "reason_codes": ["allowed" if allowed else "approval_required"],
        "effect_capability": capability,
        "broker_handle": None,
        "valid_until": "2026-08-09T12:01:00Z" if allowed else None,
        "correlation_id": _CORRELATION_ID,
    }


def _effect_detail(record: EffectRecord) -> dict[str, object]:
    return {
        **_effect_reservation(record, None),
        "run_id": _IDS["run_id"],
        "effect_key": f"document:{record.action}",
        "request_digest": record.effect_digest,
        "lifecycle_status": "reserved",
        "consumed_at": None,
        "approval_id": str(_APPROVAL_ID) if record.approved else None,
        "resume_run_reference": None,
        "updated_at": "2026-08-09T12:00:02Z",
    }


def _authorization(record: EffectRecord, *, allow: bool) -> dict[str, object]:
    return {
        "effect_id": str(record.effect_id),
        "decision": "allow" if allow else "deny",
        "reason_codes": ["allowed" if allow else "replay_detected"],
        "requester_context": _context(record) if allow else None,
        "consumption_token": f"consume-{record.action}" if allow else None,
        "correlation_id": _CORRELATION_ID,
    }


def _consumption(record: EffectRecord) -> dict[str, object]:
    return {
        "effect_id": str(record.effect_id),
        "status": "executing",
        "requester_context": _context(record),
        "correlation_id": _CORRELATION_ID,
    }


def _context(record: EffectRecord) -> dict[str, object]:
    return {
        "tenant_id": _IDS["tenant_id"],
        "subject_id": _IDS["subject_id"],
        "client_id": _IDS["client_id"],
        "workload_role_id": _IDS["role_id"],
        "workload_instance_id": _IDS["instance_id"],
        "workload_revision_id": _IDS["revision_id"],
        "resource_registration_revision_id": str(RESOURCE_REVISION_ID),
        "run_claim_id": _IDS["claim_id"],
        "activation_epoch": 3,
        "grant_id": _IDS["grant_id"],
        "run_id": _IDS["run_id"],
        "effect_id": str(record.effect_id),
        "action": record.action,
        "resource": {
            "type": "conformance.document",
            "descriptor_version": "v1",
            "id": "document:doc-7",
        },
        "audience": RESOURCE_AUDIENCE,
        "policy_version": POLICY_VERSION,
        "fencing_token": 3,
        "authority_envelope_id": _IDS["envelope_id"],
        "correlation_id": _CORRELATION_ID,
    }


def _require_exact_approval(view: RequestView, record: EffectRecord) -> None:
    checks = (
        view.headers.get("X-CSRF-Token") == CSRF_TOKEN,
        view.payload.get("effect_digest") == record.effect_digest,
        view.payload.get("policy_version") == POLICY_VERSION,
        view.payload.get("decision") == "approve",
    )
    if not all(checks):
        raise AssertionError("exact member approval is invalid")


def _require_guard_headers(view: RequestView, assertion: str) -> None:
    checks = (
        view.headers.get("Authorization", "").startswith("DPoP ey"),
        bool(view.headers.get("DPoP")),
        view.headers.get("X-Kamiwaza-Workload-Assertion") == assertion,
    )
    if not all(checks):
        raise AssertionError("raw guard authority is incomplete")


def _effect_by_id(
    effects: Mapping[str, EffectRecord],
    effect_id: object,
) -> EffectRecord:
    for record in effects.values():
        if str(record.effect_id) == str(effect_id):
            return record
    raise AssertionError("neutral effect is unknown")


def _effect_id_from_path(url: str) -> str:
    return url.split("/effects/", 1)[1].split("/", 1)[0]


def _require_active_registration(registration: Mapping[str, object]) -> None:
    checks = (
        registration.get("status") == "active",
        registration.get("revision_id") == str(RESOURCE_REVISION_ID),
        registration.get("audiences") == [RESOURCE_AUDIENCE],
    )
    if not all(checks):
        raise AssertionError("neutral resource registration is inactive")


def _run_reservation() -> dict[str, object]:
    return {
        "run_id": _IDS["run_id"],
        "status": "queued",
        "run_reference": "opaque-neutral-resource-run-reference",
        "correlation_id": _CORRELATION_ID,
        "authority_deadline": "2026-08-10T12:00:00Z",
    }


def _claim() -> dict[str, object]:
    return {
        "run_id": _IDS["run_id"],
        "claim_id": _IDS["claim_id"],
        "status": "claimed",
        "fencing_token": 3,
        "lease_expires_at": "2026-08-09T12:05:00Z",
        "run_capability": "header.neutral-run-capability.signature",
        "expires_at": "2026-08-09T12:05:00Z",
        "authority_deadline": "2026-08-10T12:00:00Z",
        "correlation_id": _CORRELATION_ID,
    }


def _transition_payload(run_status: str, claim_status: str) -> dict[str, object]:
    return {
        "run_id": _IDS["run_id"],
        "claim_id": _IDS["claim_id"],
        "run_status": run_status,
        "claim_status": claim_status,
        "lease_expires_at": "2026-08-09T12:05:00Z",
        "authority_deadline": "2026-08-10T12:00:00Z",
        "correlation_id": _CORRELATION_ID,
    }


async def _call_resource(app: Any, view: RequestView) -> StubResponse:
    sent: list[dict[str, Any]] = []
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": view.body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    target = urlsplit(view.url)
    headers = [(name.encode(), value.encode()) for name, value in view.headers.items()]
    headers.append((b"host", target.netloc.encode()))
    await app(
        {
            "type": "http",
            "method": view.method,
            "scheme": target.scheme,
            "path": target.path,
            "raw_path": target.path.encode(),
            "query_string": target.query.encode(),
            "headers": headers,
        },
        receive,
        send,
    )
    return StubResponse(sent[0]["status"], json.loads(sent[1]["body"]))


MEMBER_SUBJECT_ID = _IDS["subject_id"]

__all__ = (
    "BASE_URL",
    "CSRF_TOKEN",
    "DOCUMENT_URL",
    "MEMBER_SUBJECT_ID",
    "MUTATION_DIGEST",
    "NOW",
    "POLICY_VERSION",
    "READ_DIGEST",
    "RESOURCE_AUDIENCE",
    "NeutralResourcePlatform",
)
