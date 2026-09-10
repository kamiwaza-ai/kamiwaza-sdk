"""Local RFC 9449 verification for protected-resource requests."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import jwt
from jwt import PyJWTError
from kamiwaza_sdk.delegated_workloads.proof import BODY_DIGEST_CLAIM


_ALGORITHM = "ES256"
_TYPE = "dpop+jwt"
_MAX_AGE = timedelta(seconds=60)
_FUTURE_SKEW = timedelta(seconds=5)


class ResourceDPoPError(ValueError):
    """A proof failed locally without retaining or exposing its value."""


def _require_proof(condition: bool, message: str) -> None:
    if not condition:
        raise ResourceDPoPError(message)


@dataclass(frozen=True, slots=True, kw_only=True)
class ResourceDPoPRequest:
    capability: str
    expected_thumbprint: str
    method: str
    target_uri: str
    request_digest: str


def verify_resource_dpop(
    proof: str,
    *,
    request: ResourceDPoPRequest,
    now: datetime,
) -> None:
    header = _header(proof)
    jwk = _jwk(header)
    _require_equal(_thumbprint(jwk), request.expected_thumbprint)
    payload = _decode(proof, jwk)
    _require_lifetime(payload, now)
    bindings = (
        (payload.get("htm"), request.method.upper()),
        (payload.get("htu"), request.target_uri),
        (payload.get("ath"), _digest(request.capability.encode("ascii"))),
        (payload.get(BODY_DIGEST_CLAIM), request.request_digest),
    )
    valid_bindings = all(
        _safe_equal(candidate, expected) for candidate, expected in bindings
    )
    _require_proof(valid_bindings, "protected resource proof binding is invalid")
    replay_id = payload.get("jti")
    _require_proof(
        isinstance(replay_id, str),
        "protected resource proof replay identity is invalid",
    )
    _require_proof(
        bool(replay_id),
        "protected resource proof replay identity is invalid",
    )


def _header(proof: str) -> dict[str, Any]:
    try:
        header = cast(dict[str, Any], jwt.get_unverified_header(proof))
    except PyJWTError as exc:
        raise ResourceDPoPError("protected resource proof is invalid") from exc
    expected = (header.get("typ") == _TYPE, header.get("alg") == _ALGORITHM)
    _require_proof(all(expected), "protected resource proof type is invalid")
    return header


def _jwk(header: Mapping[str, object]) -> dict[str, Any]:
    candidate = header.get("jwk")
    message = "protected resource proof key is invalid"
    _require_proof(isinstance(candidate, dict), message)
    jwk = cast(dict[str, Any], candidate)
    required = (
        "d" not in jwk,
        jwk.get("kty") == "EC",
        jwk.get("crv") == "P-256",
    )
    _require_proof(all(required), message)
    return jwk


def _decode(proof: str, jwk: Mapping[str, object]) -> dict[str, Any]:
    try:
        key = jwt.PyJWK.from_dict(dict(jwk), algorithm=_ALGORITHM).key
        return cast(
            dict[str, Any],
            jwt.decode(
                proof,
                key,
                algorithms=[_ALGORITHM],
                options={"require": ["htu", "htm", "iat", "jti"], "verify_iat": False},
            ),
        )
    except (PyJWTError, TypeError, ValueError) as exc:
        raise ResourceDPoPError("protected resource proof is invalid") from exc


def _require_lifetime(payload: Mapping[str, object], now: datetime) -> None:
    issued_at = payload.get("iat")
    message = "protected resource proof lifetime is invalid"
    _require_proof(isinstance(issued_at, int), message)
    try:
        instant = datetime.fromtimestamp(cast(int, issued_at), timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ResourceDPoPError(message) from exc
    valid = (instant >= now - _MAX_AGE, instant <= now + _FUTURE_SKEW)
    _require_proof(all(valid), message)


def _thumbprint(jwk: Mapping[str, object]) -> str:
    coordinates = {
        "crv": "P-256",
        "kty": "EC",
        "x": _coordinate(jwk, "x"),
        "y": _coordinate(jwk, "y"),
    }
    canonical = json.dumps(coordinates, separators=(",", ":"), sort_keys=True)
    return _digest(canonical.encode("ascii"))


def _coordinate(jwk: Mapping[str, object], name: str) -> str:
    value = jwk.get(name)
    message = "protected resource proof key is invalid"
    _require_proof(isinstance(value, str), message)
    _require_proof(bool(value), message)
    return cast(str, value)


def _require_equal(candidate: str, expected: str) -> None:
    _require_proof(
        hmac.compare_digest(candidate, expected),
        "protected resource proof key is invalid",
    )


def _safe_equal(candidate: object, expected: str) -> bool:
    return isinstance(candidate, str) and hmac.compare_digest(candidate, expected)


def _digest(value: bytes) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(value).digest()).rstrip(b"=").decode()
    )
