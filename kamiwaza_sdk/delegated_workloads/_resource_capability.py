"""Local verification for dedicated protected-effect capabilities."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from jwt import InvalidAudienceError, PyJWTError


CAPABILITY_ISSUER = "urn:kamiwaza:delegated-workloads:v1"
EFFECT_CAPABILITY_TYPE = "kz-effect-cap+jwt"
_ALGORITHM = "ES256"
_KID_PREFIX = "kz-delegated-"
_FUTURE_SKEW = timedelta(seconds=5)

JwksProvider = Callable[[datetime], Mapping[str, object]]


class ResourceCapabilityError(ValueError):
    """A capability failed locally without retaining or exposing its value."""


def _require_capability(condition: bool, message: str) -> None:
    if not condition:
        raise ResourceCapabilityError(message)


@dataclass(frozen=True, slots=True)
class VerifiedEffectCapability:
    subject_id: UUID
    tenant_id: UUID
    client_id: UUID
    workload_revision_id: UUID
    workload_instance_id: UUID
    workload_role_id: UUID
    grant_id: UUID
    run_id: UUID
    run_claim_id: UUID
    effect_id: UUID
    fencing_token: int
    proof_key_thumbprint: str
    action: str
    resource: Mapping[str, object]

    @property
    def actor_id(self) -> str:
        values = (
            self.tenant_id,
            self.client_id,
            self.workload_revision_id,
            self.workload_role_id,
            self.workload_instance_id,
        )
        return "urn:kamiwaza:workload:" + ":".join(str(value) for value in values)


class EffectCapabilityVerifier:
    """Verify one effect capability against current bounded public keys."""

    def __init__(
        self,
        jwks_provider: JwksProvider,
        issuer: str = CAPABILITY_ISSUER,
    ) -> None:
        self._jwks_provider = jwks_provider
        self._issuer = issuer

    def verify(
        self,
        token: str,
        *,
        audience: str,
        now: datetime,
    ) -> VerifiedEffectCapability:
        header = _header(token)
        kid = _trusted_header(header)
        key = _public_key(self._jwks_provider(now), kid, now)
        payload = _decode(token, key, audience, self._issuer)
        _require_lifetime(payload, now)
        return _verified_claims(payload)


def _header(token: str) -> dict[str, Any]:
    try:
        return cast(dict[str, Any], jwt.get_unverified_header(token))
    except PyJWTError as exc:
        raise ResourceCapabilityError(
            "protected resource capability is invalid"
        ) from exc


def _trusted_header(header: Mapping[str, object]) -> str:
    kid = header.get("kid")
    valid = (
        header.get("typ") == EFFECT_CAPABILITY_TYPE,
        header.get("alg") == _ALGORITHM,
        isinstance(kid, str),
        isinstance(kid, str) and kid.startswith(_KID_PREFIX),
    )
    _require_capability(
        all(valid),
        "protected resource capability type is invalid",
    )
    return cast(str, kid)


def _public_key(
    document: Mapping[str, object],
    kid: str,
    now: datetime,
) -> ec.EllipticCurvePublicKey:
    candidate = _matching_key(document, kid)
    _require_capability(
        _eligible_key(candidate, now),
        "protected resource signing key is unavailable",
    )
    return _decoded_public_key(candidate)


def _matching_key(document: Mapping[str, object], kid: str) -> dict[str, Any]:
    keys = document.get("keys")
    _require_capability(
        isinstance(keys, list),
        "protected resource signing keys are invalid",
    )
    candidates = tuple(filter(lambda value: _key_matches(value, kid), cast(list, keys)))
    _require_capability(
        len(candidates) == 1,
        "protected resource signing key is unavailable",
    )
    return cast(dict[str, Any], candidates[0])


def _key_matches(candidate: object, kid: str) -> bool:
    return isinstance(candidate, dict) and candidate.get("kid") == kid


def _decoded_public_key(candidate: dict[str, Any]) -> ec.EllipticCurvePublicKey:
    try:
        key = jwt.PyJWK.from_dict(candidate, algorithm=_ALGORITHM).key
    except (PyJWTError, TypeError, ValueError) as exc:
        raise ResourceCapabilityError(
            "protected resource signing key is invalid"
        ) from exc
    _require_capability(
        isinstance(key, ec.EllipticCurvePublicKey),
        "protected resource signing key is invalid",
    )
    return cast(ec.EllipticCurvePublicKey, key)


def _eligible_key(candidate: Mapping[str, object], now: datetime) -> bool:
    verify_until = candidate.get("x-kz-verify-until")
    checks = (
        "d" not in candidate,
        candidate.get("alg") in (None, _ALGORITHM),
        candidate.get("use") in (None, "sig"),
        not isinstance(verify_until, int) or now.timestamp() < verify_until,
    )
    return all(checks)


def _decode(
    token: str,
    key: ec.EllipticCurvePublicKey,
    audience: str,
    issuer: str,
) -> dict[str, Any]:
    try:
        return cast(
            dict[str, Any],
            jwt.decode(
                token,
                key,
                algorithms=[_ALGORITHM],
                audience=audience,
                issuer=issuer,
                options={
                    "require": ["iss", "sub", "aud", "exp", "iat", "nbf", "jti"],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            ),
        )
    except InvalidAudienceError as exc:
        raise ResourceCapabilityError("protected resource audience is invalid") from exc
    except PyJWTError as exc:
        raise ResourceCapabilityError(
            "protected resource capability is invalid"
        ) from exc


def _require_lifetime(payload: Mapping[str, object], now: datetime) -> None:
    issued_at = _timestamp(payload.get("iat"))
    not_before = _timestamp(payload.get("nbf"))
    expires_at = _timestamp(payload.get("exp"))
    deadline = _timestamp(payload.get("authority_deadline"))
    valid = (
        issued_at <= now + _FUTURE_SKEW,
        not_before <= now + _FUTURE_SKEW,
        expires_at > now,
        deadline > now,
        expires_at <= deadline,
    )
    _require_capability(
        all(valid),
        "protected resource capability is expired",
    )


def _verified_claims(payload: Mapping[str, Any]) -> VerifiedEffectCapability:
    _require_effect_shape(payload)
    capability = VerifiedEffectCapability(
        subject_id=_uuid(payload, "sub"),
        tenant_id=_uuid(payload, "tenant_id"),
        client_id=_uuid(payload, "client_id"),
        workload_revision_id=_uuid(payload, "workload_revision_id"),
        workload_instance_id=_uuid(payload, "workload_instance_id"),
        workload_role_id=_uuid(payload, "actor_role_id"),
        grant_id=_uuid(payload, "grant_id"),
        run_id=_uuid(payload, "run_id"),
        run_claim_id=_uuid(payload, "run_claim_id"),
        effect_id=_uuid(payload, "effect_id"),
        fencing_token=_positive_int(payload, "fencing_token"),
        proof_key_thumbprint=_proof_thumbprint(payload),
        action=_single_string(payload, "actions"),
        resource=_single_resource(payload),
    )
    _require_capability(
        _actor(payload) == capability.actor_id,
        "protected resource actor is invalid",
    )
    return capability


def _require_effect_shape(payload: Mapping[str, Any]) -> None:
    scope = payload.get("scope")
    valid = (
        payload.get("token_type") == EFFECT_CAPABILITY_TYPE,
        payload.get("token_class") == "effect",
        isinstance(scope, list) and "effect:execute" in scope,
        isinstance(payload.get("jti"), str) and bool(payload.get("jti")),
        isinstance(payload.get("effect_key"), str) and bool(payload.get("effect_key")),
    )
    _require_capability(
        all(valid),
        "protected resource capability class is invalid",
    )


def _timestamp(value: object) -> datetime:
    _require_capability(
        isinstance(value, int),
        "protected resource capability lifetime is invalid",
    )
    try:
        return datetime.fromtimestamp(cast(int, value), timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ResourceCapabilityError(
            "protected resource capability lifetime is invalid"
        ) from exc


def _uuid(payload: Mapping[str, object], name: str) -> UUID:
    try:
        return UUID(str(payload.get(name)))
    except (TypeError, ValueError) as exc:
        raise ResourceCapabilityError(
            "protected resource capability claims are invalid"
        ) from exc


def _positive_int(payload: Mapping[str, object], name: str) -> int:
    value = payload.get(name)
    message = "protected resource capability claims are invalid"
    _require_capability(type(value) is int, message)
    result = cast(int, value)
    _require_capability(result >= 1, message)
    return result


def _proof_thumbprint(payload: Mapping[str, object]) -> str:
    confirmation = payload.get("cnf")
    value = confirmation.get("jkt") if isinstance(confirmation, dict) else None
    message = "protected resource proof binding is invalid"
    _require_capability(isinstance(value, str), message)
    _require_capability(bool(value), message)
    return cast(str, value)


def _single_string(payload: Mapping[str, object], name: str) -> str:
    values = payload.get(name)
    message = "protected resource authority is invalid"
    _require_capability(isinstance(values, list), message)
    entries = cast(list[object], values)
    _require_capability(len(entries) == 1, message)
    _require_capability(isinstance(entries[0], str), message)
    return cast(str, entries[0])


def _single_resource(payload: Mapping[str, object]) -> Mapping[str, object]:
    resources = payload.get("resources")
    message = "protected resource authority is invalid"
    _require_capability(isinstance(resources, list), message)
    entries = cast(list[object], resources)
    _require_capability(len(entries) == 1, message)
    _require_capability(isinstance(entries[0], dict), message)
    resource = cast(dict[str, object], entries[0])
    _require_resource_fields(resource, message)
    return resource


def _require_resource_fields(resource: Mapping[str, object], message: str) -> None:
    for name in ("type", "descriptor_version", "id"):
        value = resource.get(name)
        _require_capability(isinstance(value, str), message)
        _require_capability(bool(value), message)


def _actor(payload: Mapping[str, object]) -> object:
    actor = payload.get("act")
    return actor.get("sub") if isinstance(actor, dict) else None
