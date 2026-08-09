"""Cryptographic fixtures for protected-resource guard tests."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from kamiwaza_sdk.delegated_workloads.models import (
    DecisionReasonCode,
    DelegatedRequesterContext,
    EffectAuthorization,
    EffectAuthorizationDecision,
    EffectConsumption,
    EffectLifecycleStatus,
    ResourceRef,
)
from kamiwaza_sdk.delegated_workloads.proof import OneUseToken, body_digest
from kamiwaza_sdk.delegated_workloads.resource_server import (
    GuardAuthorization,
    ProtectedResourceGuard,
    ProtectedResourceRequest,
    ResourceGuardDecisionPort,
    ResourceGuardRegistration,
)


NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)
ISSUER = "urn:kamiwaza:delegated-workloads:v1"
EFFECT_TYPE = "kz-effect-cap+jwt"
RUN_TYPE = "kz-run-cap+jwt"


class DecisionStub(ResourceGuardDecisionPort):
    def __init__(
        self,
        authorization: EffectAuthorization,
        consumption: EffectConsumption,
    ) -> None:
        self.authorization = authorization
        self.consumption = consumption
        self.authorize_calls: list[GuardAuthorization] = []
        self.consume_calls: list[GuardAuthorization] = []
        self.consume_error: Exception | None = None

    def authorize(self, request: GuardAuthorization) -> EffectAuthorization:
        self.authorize_calls.append(request)
        return self.authorization

    def consume(
        self,
        request: GuardAuthorization,
        token: OneUseToken,
    ) -> EffectConsumption:
        del token
        self.consume_calls.append(request)
        if self.consume_error is not None:
            raise self.consume_error
        return self.consumption


@dataclass(frozen=True, slots=True)
class RequestOverrides:
    payload: dict[str, object] | None = None
    capability_header: dict[str, object] | None = None
    signing_key: ec.EllipticCurvePrivateKey | None = None
    proof_payload: dict[str, object] | None = None
    proof_key: ec.EllipticCurvePrivateKey | None = None
    body: bytes = b'{"value":7}'
    extra_headers: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class _ProofMaterial:
    capability: str
    key: ec.EllipticCurvePrivateKey
    target: str
    body: bytes
    overrides: dict[str, object]


@dataclass(slots=True)
class GuardCase:
    guard: ProtectedResourceGuard
    registration: ResourceGuardRegistration
    decisions: DecisionStub
    context: DelegatedRequesterContext
    signing_key: ec.EllipticCurvePrivateKey
    proof_key: ec.EllipticCurvePrivateKey
    kid: str

    def request(
        self,
        overrides: RequestOverrides = RequestOverrides(),
    ) -> ProtectedResourceRequest:
        target = self.registration.audience + "/tickets/TKT-7"
        capability = _capability(
            self,
            overrides.payload or {},
            overrides.capability_header or {},
            overrides.signing_key or self.signing_key,
        )
        proof = _proof(
            _ProofMaterial(
                capability,
                overrides.proof_key or self.proof_key,
                target,
                overrides.body,
                overrides.proof_payload or {},
            )
        )
        headers = {
            "Authorization": f"DPoP {capability}",
            "DPoP": proof,
            "X-Kamiwaza-Workload-Assertion": "ambient-workload-assertion",
            **(overrides.extra_headers or {}),
        }
        return ProtectedResourceRequest(
            method="POST",
            target_uri=target,
            body=overrides.body,
            headers=headers,
        )


def guard_case() -> GuardCase:
    ids = _ids()
    registration = ResourceGuardRegistration(
        resource_type="conformance.ticket",
        descriptor_version="v1",
        revision_id=ids["resource_revision_id"],
        audience="https://tickets.example.test",
        action="dispatch",
        guard_contract_version="guard:v1",
    )
    context = _context(ids, registration)
    authorization = EffectAuthorization(
        effect_id=ids["effect_id"],
        decision=EffectAuthorizationDecision.ALLOW,
        reason_codes=(DecisionReasonCode.ALLOWED,),
        requester_context=context,
        consumption_token=OneUseToken("consume-once"),
        correlation_id=ids["correlation_id"],
    )
    consumption = EffectConsumption(
        effect_id=ids["effect_id"],
        status=EffectLifecycleStatus.EXECUTING,
        requester_context=context,
        correlation_id=ids["correlation_id"],
    )
    signing_key = ec.generate_private_key(ec.SECP256R1())
    proof_key = ec.generate_private_key(ec.SECP256R1())
    kid = "kz-delegated-resource-guard"
    decisions = DecisionStub(authorization, consumption)
    jwks = {"keys": [{**_jwk(signing_key.public_key()), "kid": kid, "alg": "ES256"}]}
    guard = ProtectedResourceGuard(lambda _now: jwks, decisions, clock=lambda: NOW)
    return GuardCase(
        guard,
        registration,
        decisions,
        context,
        signing_key,
        proof_key,
        kid,
    )


def denied_authorization(case: GuardCase) -> EffectAuthorization:
    return EffectAuthorization(
        effect_id=case.context.effect_id,
        decision=EffectAuthorizationDecision.DENY,
        reason_codes=(DecisionReasonCode.CURRENT_AUTHORITY_DENIED,),
        requester_context=None,
        consumption_token=None,
        correlation_id=case.context.correlation_id,
    )


def _capability(
    case: GuardCase,
    overrides: dict[str, object],
    header_overrides: dict[str, object],
    key: ec.EllipticCurvePrivateKey,
) -> str:
    context = case.context
    actor = _actor(context)
    payload: dict[str, object] = {
        "iss": ISSUER,
        "sub": str(context.subject_id),
        "act": {"sub": actor},
        "aud": case.registration.audience,
        "iat": int(NOW.timestamp()),
        "nbf": int(NOW.timestamp()),
        "exp": int((NOW + timedelta(seconds=45)).timestamp()),
        "authority_deadline": int((NOW + timedelta(minutes=5)).timestamp()),
        "jti": "effect-capability-jti",
        "token_type": EFFECT_TYPE,
        "token_class": "effect",
        "tenant_id": str(context.tenant_id),
        "client_id": str(context.client_id),
        "automation_revision": "sha256:" + "a" * 64,
        "workload_revision_id": str(context.workload_revision_id),
        "workload_instance_id": str(context.workload_instance_id),
        "actor_role_id": str(context.workload_role_id),
        "grant_id": str(context.grant_id),
        "run_id": str(context.run_id),
        "run_claim_id": str(context.run_claim_id),
        "fencing_token": context.fencing_token,
        "scope": ["effect:execute"],
        "actions": [f"{case.registration.resource_type}:{case.registration.action}"],
        "resources": [context.resource.model_dump(mode="json")],
        "credential_binding_ids": [],
        "cnf": {"jkt": _thumbprint(_jwk(case.proof_key.public_key()))},
        "effect_id": str(context.effect_id),
        "effect_key": "ticket:dispatch",
    }
    payload.update(overrides)
    headers = {"kid": case.kid, "typ": EFFECT_TYPE, **header_overrides}
    return jwt.encode(payload, key, algorithm="ES256", headers=headers)


def _proof(
    material: _ProofMaterial,
) -> str:
    payload: dict[str, object] = {
        "htu": material.target,
        "htm": "POST",
        "iat": int(NOW.timestamp()),
        "jti": "proof-jti",
        "ath": _encoded_digest(material.capability.encode("ascii")),
        "body_sha256": body_digest(material.body),
    }
    payload.update(material.overrides)
    return jwt.encode(
        payload,
        material.key,
        algorithm="ES256",
        headers={
            "typ": "dpop+jwt",
            "jwk": _jwk(material.key.public_key()),
        },
    )


def _context(
    ids: dict[str, UUID],
    registration: ResourceGuardRegistration,
) -> DelegatedRequesterContext:
    return DelegatedRequesterContext(
        tenant_id=ids["tenant_id"],
        subject_id=ids["subject_id"],
        client_id=ids["client_id"],
        workload_role_id=ids["role_id"],
        workload_instance_id=ids["instance_id"],
        workload_revision_id=ids["workload_revision_id"],
        resource_registration_revision_id=registration.revision_id,
        run_claim_id=ids["run_claim_id"],
        activation_epoch=3,
        grant_id=ids["grant_id"],
        run_id=ids["run_id"],
        effect_id=ids["effect_id"],
        action=registration.action,
        resource=ResourceRef(
            type=registration.resource_type,
            descriptor_version=registration.descriptor_version,
            id="ticket:7",
        ),
        audience=registration.audience,
        policy_version="ticket-policy:v1",
        fencing_token=7,
        authority_envelope_id=ids["envelope_id"],
        correlation_id=ids["correlation_id"],
    )


def _ids() -> dict[str, UUID]:
    names = (
        "tenant_id",
        "subject_id",
        "client_id",
        "role_id",
        "instance_id",
        "workload_revision_id",
        "resource_revision_id",
        "run_claim_id",
        "grant_id",
        "run_id",
        "effect_id",
        "envelope_id",
        "correlation_id",
    )
    return {name: uuid4() for name in names}


def _actor(context: DelegatedRequesterContext) -> str:
    values = (
        context.tenant_id,
        context.client_id,
        context.workload_revision_id,
        context.workload_role_id,
        context.workload_instance_id,
    )
    return "urn:kamiwaza:workload:" + ":".join(str(value) for value in values)


def _jwk(key: ec.EllipticCurvePublicKey) -> dict[str, str]:
    numbers = key.public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": _encode(numbers.x.to_bytes(32, "big")),
        "y": _encode(numbers.y.to_bytes(32, "big")),
    }


def _thumbprint(jwk: dict[str, str]) -> str:
    canonical = b'{"crv":"P-256","kty":"EC","x":"' + jwk["x"].encode()
    canonical += b'","y":"' + jwk["y"].encode() + b'"}'
    return _encoded_digest(canonical)


def _encoded_digest(value: bytes) -> str:
    return _encode(hashlib.sha256(value).digest())


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
