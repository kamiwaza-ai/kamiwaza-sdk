"""Framework-neutral protected-resource guard and direct Core adapter."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, TypeVar, cast
from urllib.parse import urlsplit
from uuid import UUID

from kamiwaza_sdk.delegated_workloads._protocol import json_bytes, validated
from kamiwaza_sdk.delegated_workloads._resource_capability import (
    EffectCapabilityVerifier,
    JwksProvider,
    ResourceCapabilityError,
    VerifiedEffectCapability,
)
from kamiwaza_sdk.delegated_workloads._resource_dpop import verify_resource_dpop
from kamiwaza_sdk.delegated_workloads._resource_dpop import ResourceDPoPRequest
from kamiwaza_sdk.delegated_workloads.models import (
    DelegatedRequesterContext,
    EffectAuthorization,
    EffectAuthorizationDecision,
    EffectConsumption,
    EffectLifecycleStatus,
)
from kamiwaza_sdk.delegated_workloads.proof import (
    DPoPProof,
    DelegatedCapability,
    OneUseToken,
    WorkloadAssertion,
    _secret_value,
    body_digest,
)
from kamiwaza_sdk.delegated_workloads.transport import (
    SessionPort,
    checked_json_response,
)


_ResultT = TypeVar("_ResultT")
_HandlerT = Callable[["ProtectedResourceRequest", "SealedDelegatedContext"], _ResultT]
_CONTEXT_SEAL = object()
_DELEGATED_PREFIX = "x-kamiwaza-delegated-"


class ResourceGuardRejected(PermissionError):
    """A safe denial raised before protected application code runs."""

    def __init__(self) -> None:
        super().__init__("protected resource request was rejected")


@dataclass(frozen=True, slots=True, kw_only=True)
class ResourceGuardRegistration:
    resource_type: str
    descriptor_version: str
    revision_id: UUID
    audience: str
    action: str
    guard_contract_version: str

    def __post_init__(self) -> None:
        if not _registration_valid(self):
            raise ValueError("protected resource registration is invalid")


def _registration_valid(registration: ResourceGuardRegistration) -> bool:
    audience = urlsplit(registration.audience)
    checks = (
        bool(registration.resource_type),
        registration.descriptor_version.startswith("v"),
        audience.scheme == "https",
        bool(audience.netloc),
        audience.path in ("", "/"),
        not audience.query,
        not audience.fragment,
        bool(registration.action),
        ":v" in registration.guard_contract_version,
    )
    return all(checks)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProtectedResourceRequest:
    method: str
    target_uri: str
    body: bytes = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)

    def __post_init__(self) -> None:
        if not self.method:
            raise ValueError("protected resource request is incomplete")
        if not self.target_uri:
            raise ValueError("protected resource request is incomplete")
        if not isinstance(self.body, bytes):
            raise ValueError("protected resource request is incomplete")

    @property
    def request_digest(self) -> str:
        return body_digest(self.body)


@dataclass(frozen=True, slots=True)
class _GuardCredentials:
    capability: DelegatedCapability = field(repr=False)
    proof: DPoPProof = field(repr=False)
    workload_assertion: WorkloadAssertion = field(repr=False)


@dataclass(frozen=True, slots=True)
class _GuardRuntime:
    verifier: EffectCapabilityVerifier
    decisions: ResourceGuardDecisionPort
    now: datetime


@dataclass(frozen=True, slots=True)
class GuardAuthorization:
    effect_id: UUID
    request_digest: str
    method: str
    target_uri: str
    fencing_token: int
    credentials: _GuardCredentials = field(repr=False)


class ResourceGuardDecisionPort(Protocol):
    def authorize(self, request: GuardAuthorization) -> EffectAuthorization: ...

    def consume(
        self,
        request: GuardAuthorization,
        token: OneUseToken,
    ) -> EffectConsumption: ...


class DelegatedResourceServer(Protocol):
    """Guard handlers through the active platform resource contract."""

    def guard(
        self,
        registration: ResourceGuardRegistration,
        handler: _HandlerT[_ResultT],
    ) -> Callable[[ProtectedResourceRequest], _ResultT]: ...


class SealedDelegatedContext:
    """Read-only dual-principal context constructible only by a verified guard."""

    __slots__ = ("_context",)
    _context: DelegatedRequesterContext

    def __init__(
        self,
        context: DelegatedRequesterContext,
        seal: object | None = None,
    ) -> None:
        if seal is not _CONTEXT_SEAL:
            raise TypeError("delegated requester context is not verified")
        object.__setattr__(self, "_context", context)

    @classmethod
    def _verified(cls, context: DelegatedRequesterContext) -> SealedDelegatedContext:
        return cls(context, _CONTEXT_SEAL)

    @property
    def context(self) -> DelegatedRequesterContext:
        return self._context

    @property
    def subject_id(self) -> UUID:
        return self._context.subject_id

    @property
    def actor_id(self) -> str:
        context = self._context
        values = (
            context.tenant_id,
            context.client_id,
            context.workload_revision_id,
            context.workload_role_id,
            context.workload_instance_id,
        )
        return "urn:kamiwaza:workload:" + ":".join(str(value) for value in values)

    def __repr__(self) -> str:
        return "SealedDelegatedContext(verified=True)"


class ProtectedResourceGuard:
    """Verify local request authority, then consume current Core authority."""

    def __init__(
        self,
        jwks_provider: JwksProvider,
        decisions: ResourceGuardDecisionPort,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._verifier = EffectCapabilityVerifier(jwks_provider)
        self._decisions = decisions
        self._clock = clock

    def guard(
        self,
        registration: ResourceGuardRegistration,
        handler: _HandlerT[_ResultT],
    ) -> Callable[[ProtectedResourceRequest], _ResultT]:
        def guarded(request: ProtectedResourceRequest) -> _ResultT:
            context = self._verified_context(registration, request)
            return handler(request, context)

        return guarded

    def _verified_context(
        self,
        registration: ResourceGuardRegistration,
        request: ProtectedResourceRequest,
    ) -> SealedDelegatedContext:
        try:
            return _authorize_and_consume(
                _GuardRuntime(self._verifier, self._decisions, self._clock()),
                registration,
                request,
            )
        except ResourceGuardRejected:
            raise
        except Exception:
            raise ResourceGuardRejected() from None


class CoreResourceGuardHTTPClient:
    """Forward locally verified raw authority to Core decision/consumption APIs."""

    def __init__(self, base_url: str, session: SessionPort) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session

    def authorize(self, request: GuardAuthorization) -> EffectAuthorization:
        body = {
            "effect_id": str(request.effect_id),
            "request_digest": request.request_digest,
            "method": request.method,
            "target_uri": request.target_uri,
        }
        payload = self._post("/effect-authorizations", body, request)
        return validated(EffectAuthorization, payload)

    def consume(
        self,
        request: GuardAuthorization,
        token: OneUseToken,
    ) -> EffectConsumption:
        body = {
            "request_digest": request.request_digest,
            "fencing_token": request.fencing_token,
        }
        payload = self._post(
            f"/effects/{request.effect_id}/consumption",
            body,
            request,
            token,
        )
        return validated(EffectConsumption, payload)

    def _post(
        self,
        path: str,
        body: Mapping[str, object],
        request: GuardAuthorization,
        token: OneUseToken | None = None,
    ) -> object:
        response = self._session.request(
            "POST",
            self._base_url + path,
            data=json_bytes(body),
            headers=_core_headers(request.credentials, token),
        )
        return checked_json_response(response)


def _authorize_and_consume(
    runtime: _GuardRuntime,
    registration: ResourceGuardRegistration,
    request: ProtectedResourceRequest,
) -> SealedDelegatedContext:
    credentials = _credentials(request.headers)
    raw_capability = _secret_value(credentials.capability)
    capability = runtime.verifier.verify(
        raw_capability,
        audience=registration.audience,
        now=runtime.now,
    )
    _require_registration(capability, registration, request.target_uri)
    verify_resource_dpop(
        _secret_value(credentials.proof),
        request=ResourceDPoPRequest(
            capability=raw_capability,
            expected_thumbprint=capability.proof_key_thumbprint,
            method=request.method,
            target_uri=request.target_uri,
            request_digest=request.request_digest,
        ),
        now=runtime.now,
    )
    check = GuardAuthorization(
        capability.effect_id,
        request.request_digest,
        request.method,
        request.target_uri,
        capability.fencing_token,
        credentials,
    )
    context, token = _allowed_context(
        runtime.decisions.authorize(check), capability, registration
    )
    consumed = runtime.decisions.consume(check, token)
    _require_consumed_context(consumed, context, capability, registration)
    return SealedDelegatedContext._verified(context)


def _credentials(headers: Mapping[str, str]) -> _GuardCredentials:
    normalized = {name.lower(): value for name, value in headers.items()}
    if _contains_untrusted_context(normalized):
        raise ResourceGuardRejected()
    return _GuardCredentials(
        DelegatedCapability(_capability_header(normalized)),
        DPoPProof(_required_header(normalized, "dpop")),
        WorkloadAssertion(
            _required_header(normalized, "x-kamiwaza-workload-assertion")
        ),
    )


def _contains_untrusted_context(headers: Mapping[str, str]) -> bool:
    return any(name.startswith(_DELEGATED_PREFIX) for name in headers)


def _capability_header(headers: Mapping[str, str]) -> str:
    authorization = headers.get("authorization", "")
    scheme, separator, capability = authorization.partition(" ")
    if scheme != "DPoP":
        raise ResourceGuardRejected()
    if not separator or not capability:
        raise ResourceGuardRejected()
    return capability


def _required_header(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name)
    if not value:
        raise ResourceGuardRejected()
    return value


def _require_registration(
    capability: VerifiedEffectCapability,
    registration: ResourceGuardRegistration,
    target_uri: str,
) -> None:
    resource = capability.resource
    origin = urlsplit(target_uri)
    audience = f"{origin.scheme}://{origin.netloc}"
    checks = (
        audience == registration.audience,
        resource.get("type") == registration.resource_type,
        resource.get("descriptor_version") == registration.descriptor_version,
        capability.action == f"{registration.resource_type}:{registration.action}",
    )
    if not all(checks):
        raise ResourceCapabilityError("protected resource registration is invalid")


def _allowed_context(
    result: EffectAuthorization,
    capability: VerifiedEffectCapability,
    registration: ResourceGuardRegistration,
) -> tuple[DelegatedRequesterContext, OneUseToken]:
    context = result.requester_context
    token = result.consumption_token
    if result.decision is not EffectAuthorizationDecision.ALLOW:
        raise ResourceGuardRejected()
    if context is None or token is None:
        raise ResourceGuardRejected()
    _require_context(context, capability, registration)
    return context, token


def _require_consumed_context(
    result: EffectConsumption,
    authorized: DelegatedRequesterContext,
    capability: VerifiedEffectCapability,
    registration: ResourceGuardRegistration,
) -> None:
    context = result.requester_context
    if result.status is not EffectLifecycleStatus.EXECUTING or context != authorized:
        raise ResourceGuardRejected()
    _require_context(cast(DelegatedRequesterContext, context), capability, registration)


def _require_context(
    context: DelegatedRequesterContext,
    capability: VerifiedEffectCapability,
    registration: ResourceGuardRegistration,
) -> None:
    resource = capability.resource
    checks = (
        context.subject_id == capability.subject_id,
        context.tenant_id == capability.tenant_id,
        context.client_id == capability.client_id,
        context.workload_revision_id == capability.workload_revision_id,
        context.workload_instance_id == capability.workload_instance_id,
        context.workload_role_id == capability.workload_role_id,
        context.grant_id == capability.grant_id,
        context.run_id == capability.run_id,
        context.run_claim_id == capability.run_claim_id,
        context.effect_id == capability.effect_id,
        context.fencing_token == capability.fencing_token,
        context.resource_registration_revision_id == registration.revision_id,
        context.resource.type == resource.get("type"),
        context.resource.descriptor_version == resource.get("descriptor_version"),
        context.resource.id == resource.get("id"),
        context.action == registration.action,
        context.audience == registration.audience,
    )
    if not all(checks):
        raise ResourceGuardRejected()


def _core_headers(
    credentials: _GuardCredentials,
    token: OneUseToken | None,
) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"DPoP {_secret_value(credentials.capability)}",
        "DPoP": _secret_value(credentials.proof),
        "X-Kamiwaza-Workload-Assertion": _secret_value(credentials.workload_assertion),
    }
    if token is not None:
        headers["X-Kamiwaza-Effect-Consumption"] = _secret_value(token)
    return headers


__all__ = (
    "CoreResourceGuardHTTPClient",
    "DelegatedResourceServer",
    "GuardAuthorization",
    "ProtectedResourceGuard",
    "ProtectedResourceRequest",
    "ResourceGuardDecisionPort",
    "ResourceGuardRegistration",
    "ResourceGuardRejected",
    "SealedDelegatedContext",
)
