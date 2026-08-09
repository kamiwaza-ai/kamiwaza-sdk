"""Ephemeral DPoP key and exact request-proof construction."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import ec


@dataclass(frozen=True, slots=True, kw_only=True)
class DPoPProofRequest:
    """One exact protocol request covered by a fresh proof."""

    method: str
    target_uri: str
    access_token: str | None = field(default=None, repr=False)
    body_digest: str
    issued_at: datetime
    nonce: str | None = None


class DPoPProofKey:
    """One in-memory ephemeral proof key; private material is never serialized."""

    def __init__(self, private_key: ec.EllipticCurvePrivateKey) -> None:
        self._private_key = private_key

    @classmethod
    def generate(cls) -> DPoPProofKey:
        return cls(ec.generate_private_key(ec.SECP256R1()))

    def create(self, request: DPoPProofRequest) -> str:
        claims: dict[str, object] = {
            "htu": request.target_uri,
            "htm": request.method.upper(),
            "iat": int(request.issued_at.timestamp()),
            "jti": str(uuid4()),
            "body_sha256": request.body_digest,
        }
        if request.access_token is not None:
            claims["ath"] = _access_token_hash(request.access_token)
        if request.nonce is not None:
            claims["nonce"] = request.nonce
        jwk = _public_jwk(self._private_key.public_key())
        return jwt.encode(
            claims,
            self._private_key,
            algorithm="ES256",
            headers={"typ": "dpop+jwt", "jwk": jwk},
        )


def body_digest(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _access_token_hash(token: str) -> str:
    return _base64url(hashlib.sha256(token.encode()).digest())


def _public_jwk(key: ec.EllipticCurvePublicKey) -> dict[str, str]:
    numbers = key.public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": _base64url(numbers.x.to_bytes(32, "big")),
        "y": _base64url(numbers.y.to_bytes(32, "big")),
    }


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()
