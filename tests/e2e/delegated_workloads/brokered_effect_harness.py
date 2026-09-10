"""Deterministic Core boundary behind the real typed broker SDK client."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import cast
from uuid import UUID

import jwt

from kamiwaza_sdk.delegated_workloads import (
    CredentialBroker,
    CredentialOperationParameters,
    CredentialUseRequest,
    DelegatedExecutorClient,
    DelegatedRunAuthority,
    DelegatedWorkloadTransport,
    DestinationRef,
    EffectReservationRequest,
    EffectResourceRef,
)
from kamiwaza_sdk.delegated_workloads.proof import BODY_DIGEST_CLAIM, body_digest

from .fixtures.fake_provider import (
    BrokerResourceCall,
    ClosedBrokerResource,
    ClosedOperationRejected,
    FakeOAuthProvider,
    ProviderCredentialRejected,
    ProviderResponseLost,
)

BASE_URL = "https://core.example.test/api/v1/delegated-workloads"
RUN_ID = "11111111-1111-4111-8111-111111111111"
CLAIM_ID = "22222222-2222-4222-8222-222222222222"
EFFECT_ID = "33333333-3333-4333-8333-333333333333"
BINDING_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
LEASE_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
CORRELATION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
RUN_CAPABILITY = "header.run-capability.signature"
EFFECT_CAPABILITY = "header.effect-capability.signature"
BROKER_HANDLE = "opaque.broker-handle.signature"
ASSERTION = "projected-executor-assertion"
PROVIDER_ACCESS_CANARY = "provider-access-canary-never-emit"
PROVIDER_REVOCATION_CANARY = "provider-revocation-canary-never-emit"
NOW = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
_EFFECT_URL = f"{BASE_URL}/runs/{RUN_ID}/effects"
_USE_URL = f"{BASE_URL}/credential-uses"


@dataclass(frozen=True, slots=True)
class SafeBrokerEvent:
    effect_id: UUID
    binding_id: UUID
    lease_id: UUID | None
    status: str


@dataclass(frozen=True, slots=True)
class _RequestView:
    method: str
    url: str
    body: bytes
    headers: Mapping[str, str]
    payload: Mapping[str, object]


class _CoreResponse:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self._body = body

    def json(self) -> object:
        return self._body


class _CoreRequestRejected(RuntimeError):
    """A caller-controlled broker envelope failed local validation."""


class _BrokerCoreSession:
    """One in-memory Core replica with durable one-use broker state."""

    def __init__(self, resource: ClosedBrokerResource) -> None:
        self._resource = resource
        self._effect_fingerprint: tuple[object, ...] | None = None
        self._terminal_status: str | None = None
        self._binding_active = True
        self.safe_events: list[SafeBrokerEvent] = []

    def request(self, method: str, url: str, **kwargs: object) -> _CoreResponse:
        view = _request_view(method, url, kwargs)
        _assert_exact_proof(view)
        if view.url == _EFFECT_URL:
            return self._reserve_effect(view)
        if view.url == _USE_URL:
            return self._use_credential(view)
        raise AssertionError("broker journey route is unavailable")

    def revoke_binding(self, binding_id: UUID) -> None:
        if binding_id != UUID(BINDING_ID):
            raise AssertionError("broker journey binding is unavailable")
        self._binding_active = False
        self._resource.revoke()

    def _reserve_effect(self, view: _RequestView) -> _CoreResponse:
        _require_capability(view, RUN_CAPABILITY)
        fingerprint = _effect_fingerprint(view.payload)
        if self._effect_fingerprint is None:
            self._effect_fingerprint = fingerprint
            self.safe_events.append(_safe_event(None, "reserved"))
        elif self._effect_fingerprint != fingerprint:
            return _error_response(409, "effect_digest_conflict")
        return _CoreResponse(200, _effect_payload())

    def _use_credential(self, view: _RequestView) -> _CoreResponse:
        _require_capability(view, EFFECT_CAPABILITY)
        try:
            call = _credential_call(view.payload, self._resource)
        except (_CoreRequestRejected, ClosedOperationRejected):
            return _error_response(422, "invalid_request")
        if self._terminal_status is not None:
            return _error_response(409, "replay_rejected")
        if not self._binding_active:
            return _error_response(503, "credential_binding_unavailable")
        try:
            result = self._resource.execute(call)
        except ProviderResponseLost:
            return self._finish("ambiguous", {})
        except ProviderCredentialRejected:
            return _error_response(503, "credential_binding_unavailable")
        return self._finish("succeeded", result)

    def _finish(
        self,
        status: str,
        result: Mapping[str, object],
    ) -> _CoreResponse:
        self._terminal_status = status
        self.safe_events.append(_safe_event(UUID(LEASE_ID), status))
        return _CoreResponse(200, _receipt_payload(status, result))


@dataclass(frozen=True, slots=True)
class BrokerJourneyHarness:
    executor: DelegatedExecutorClient
    broker: CredentialBroker
    run_authority: DelegatedRunAuthority
    provider: FakeOAuthProvider
    resource: ClosedBrokerResource
    _session: _BrokerCoreSession

    @classmethod
    def create(cls) -> BrokerJourneyHarness:
        provider = FakeOAuthProvider.seeded(
            access_credential=PROVIDER_ACCESS_CANARY,
            revocation_handle=PROVIDER_REVOCATION_CANARY,
            clock=lambda: NOW,
        )
        resource = ClosedBrokerResource.connect(
            provider,
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )
        session = _BrokerCoreSession(resource)
        transport = DelegatedWorkloadTransport(session, clock=lambda: NOW)
        return cls(
            DelegatedExecutorClient(BASE_URL, transport),
            CredentialBroker(BASE_URL, transport),
            _run_authority(),
            provider,
            resource,
            session,
        )

    @property
    def safe_events(self) -> tuple[SafeBrokerEvent, ...]:
        return tuple(self._session.safe_events)

    def read_requests(
        self,
        effect_key: str,
    ) -> tuple[EffectReservationRequest, CredentialUseRequest]:
        call = BrokerResourceCall(
            operation_id="fake.documents.get",
            resource_id="doc-7",
            params={"projection": "summary"},
        )
        return self._requests(effect_key, call)

    def update_requests(
        self,
        effect_key: str,
        content: str,
    ) -> tuple[EffectReservationRequest, CredentialUseRequest]:
        call = BrokerResourceCall(
            operation_id="fake.documents.update",
            resource_id="doc-7",
            body={"content": content},
        )
        return self._requests(effect_key, call)

    def revoke_binding(self, binding_id: UUID) -> None:
        self._session.revoke_binding(binding_id)

    def _requests(
        self,
        effect_key: str,
        call: BrokerResourceCall,
    ) -> tuple[EffectReservationRequest, CredentialUseRequest]:
        digest = self.resource.request_digest(call)
        effect = _effect_request(effect_key, call.operation_id, digest)
        use = _use_request(call, digest)
        return effect, use


def _run_authority() -> DelegatedRunAuthority:
    return DelegatedRunAuthority(
        run_id=UUID(RUN_ID),
        claim_id=UUID(CLAIM_ID),
        fencing_token=3,
        capability=RUN_CAPABILITY,
        workload_assertion=ASSERTION,
    )


def _effect_request(
    effect_key: str,
    operation_id: str,
    digest: str,
) -> EffectReservationRequest:
    return EffectReservationRequest(
        effect_key=effect_key,
        effect_digest=digest,
        action=operation_id,
        resource=EffectResourceRef(
            type="fake.document",
            descriptor_version="v1",
            id="doc-7",
        ),
        audience="https://fake-provider.example.test",
        destination=DestinationRef(
            host="fake-provider.example.test",
            port=443,
            route_template="/documents/{resource_id}",
        ),
        credential_binding_id=UUID(BINDING_ID),
    )


def _use_request(call: BrokerResourceCall, digest: str) -> CredentialUseRequest:
    return CredentialUseRequest(
        credential_binding_id=UUID(BINDING_ID),
        operation_id=call.operation_id,
        request_digest=digest,
        parameters=CredentialOperationParameters(
            params=_dict_or_none(call.params),
            body=_dict_or_none(call.body),
            resource_id=call.resource_id,
        ),
    )


def _request_view(
    method: str,
    url: str,
    kwargs: Mapping[str, object],
) -> _RequestView:
    body = kwargs.get("data")
    headers = kwargs.get("headers")
    if not isinstance(body, bytes) or not isinstance(headers, Mapping):
        raise AssertionError("broker journey request is invalid")
    payload = json.loads(body)
    if not isinstance(payload, Mapping):
        raise AssertionError("broker journey body is invalid")
    return _RequestView(method, url, body, cast(Mapping[str, str], headers), payload)


def _assert_exact_proof(view: _RequestView) -> None:
    encoded = view.headers["DPoP"]
    claims = jwt.decode(encoded, options={"verify_signature": False})
    assert (claims["htm"], claims["htu"]) == (view.method, view.url)
    assert claims[BODY_DIGEST_CLAIM] == body_digest(view.body)
    authorization = view.headers.get("Authorization")
    if authorization is None:
        raise AssertionError("broker journey capability is missing")
    assert claims["ath"] == _token_hash(authorization.removeprefix("DPoP "))


def _require_capability(view: _RequestView, capability: str) -> None:
    assert view.method == "POST"
    assert view.headers["Authorization"] == f"DPoP {capability}"


def _effect_fingerprint(payload: Mapping[str, object]) -> tuple[object, ...]:
    return (
        payload["effect_key"],
        payload["effect_digest"],
        payload["action"],
        payload["credential_binding_id"],
    )


def _credential_call(
    payload: Mapping[str, object],
    resource: ClosedBrokerResource,
) -> BrokerResourceCall:
    expected = (EFFECT_ID, BINDING_ID, BROKER_HANDLE)
    actual = (
        payload.get("effect_id"),
        payload.get("credential_binding_id"),
        payload.get("broker_handle"),
    )
    if actual != expected:
        raise _CoreRequestRejected
    call = _provider_call(payload)
    if payload.get("request_digest") != resource.request_digest(call):
        raise _CoreRequestRejected
    return call


def _provider_call(payload: Mapping[str, object]) -> BrokerResourceCall:
    parameters = payload.get("parameters")
    if not isinstance(parameters, Mapping):
        raise _CoreRequestRejected
    operation_id = payload.get("operation_id")
    resource_id = parameters.get("resource_id")
    if not isinstance(operation_id, str) or not isinstance(resource_id, str):
        raise _CoreRequestRejected
    return BrokerResourceCall(
        operation_id=operation_id,
        resource_id=resource_id,
        params=_mapping_or_none(parameters.get("params")),
        body=_mapping_or_none(parameters.get("body")),
    )


def _mapping_or_none(value: object) -> Mapping[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise _CoreRequestRejected
    return cast(Mapping[str, object], value)


def _dict_or_none(value: Mapping[str, object] | None) -> dict[str, object] | None:
    return dict(value) if value is not None else None


def _effect_payload() -> dict[str, object]:
    return {
        "effect_id": EFFECT_ID,
        "decision": "allow",
        "status": "reserved",
        "policy_version": "policy-v1",
        "reason_codes": ["allowed"],
        "effect_capability": EFFECT_CAPABILITY,
        "broker_handle": BROKER_HANDLE,
        "valid_until": "2026-08-09T12:01:00Z",
        "correlation_id": CORRELATION_ID,
    }


def _receipt_payload(
    status: str,
    result: Mapping[str, object],
) -> dict[str, object]:
    return {
        "lease_id": LEASE_ID,
        "status": status,
        "result": dict(result),
        "correlation_id": CORRELATION_ID,
    }


def _error_response(status: int, code: str) -> _CoreResponse:
    return _CoreResponse(
        status,
        {
            "error": {
                "code": code,
                "message": "safe broker journey failure",
                "retry_classification": "never",
                "correlation_id": CORRELATION_ID,
                "safe_details": {},
            }
        },
    )


def _safe_event(lease_id: UUID | None, status: str) -> SafeBrokerEvent:
    return SafeBrokerEvent(
        effect_id=UUID(EFFECT_ID),
        binding_id=UUID(BINDING_ID),
        lease_id=lease_id,
        status=status,
    )


def _token_hash(value: str) -> str:
    digest = hashlib.sha256(value.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


__all__ = (
    "BINDING_ID",
    "EFFECT_ID",
    "PROVIDER_ACCESS_CANARY",
    "PROVIDER_REVOCATION_CANARY",
    "BrokerJourneyHarness",
    "SafeBrokerEvent",
)
