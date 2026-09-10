"""P-256 capability material for the neutral resource journey."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from kamiwaza_sdk.delegated_workloads._resource_capability import (
    CAPABILITY_ISSUER,
    EFFECT_CAPABILITY_TYPE,
)


@dataclass(frozen=True, slots=True)
class CapabilityMaterial:
    action: str
    effect_id: UUID
    proof_thumbprint: str
    resource_id: str
    identities: Mapping[str, str]


class CapabilitySigner:
    """Own one Core signing key and expose only its public JWKS document."""

    def __init__(self, now: datetime) -> None:
        self._key = ec.generate_private_key(ec.SECP256R1())
        self._kid = "kz-delegated-neutral-resource-v1"
        self._now = now

    def jwks(self) -> Mapping[str, object]:
        return {
            "keys": [
                {
                    **_public_jwk(self._key.public_key()),
                    "kid": self._kid,
                    "alg": "ES256",
                    "use": "sig",
                }
            ]
        }

    def mint(self, material: CapabilityMaterial) -> str:
        payload = _capability_payload(material, self._now)
        return cast(
            str,
            jwt.encode(
                payload,
                self._key,
                algorithm="ES256",
                headers={"kid": self._kid, "typ": EFFECT_CAPABILITY_TYPE},
            ),
        )


def proof_thumbprint(jwk: Mapping[str, object]) -> str:
    coordinates = {
        "crv": "P-256",
        "kty": "EC",
        "x": jwk["x"],
        "y": jwk["y"],
    }
    encoded = json.dumps(coordinates, separators=(",", ":"), sort_keys=True)
    return _digest(encoded.encode("ascii"))


def _capability_payload(
    material: CapabilityMaterial,
    now: datetime,
) -> dict[str, object]:
    ids = material.identities
    actor = "urn:kamiwaza:workload:" + ":".join(
        ids[name] for name in ("tenant_id", "client_id", "revision_id", "role_id", "instance_id")
    )
    return {
        "iss": CAPABILITY_ISSUER,
        "sub": ids["subject_id"],
        "act": {"sub": actor},
        "aud": "https://documents.example.test",
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=45)).timestamp()),
        "authority_deadline": int((now + timedelta(minutes=5)).timestamp()),
        "jti": f"neutral-{material.action}-capability",
        "token_type": EFFECT_CAPABILITY_TYPE,
        "token_class": "effect",
        "tenant_id": ids["tenant_id"],
        "client_id": ids["client_id"],
        "automation_revision": "sha256:" + "a" * 64,
        "workload_revision_id": ids["revision_id"],
        "workload_instance_id": ids["instance_id"],
        "actor_role_id": ids["role_id"],
        "grant_id": ids["grant_id"],
        "run_id": ids["run_id"],
        "run_claim_id": ids["claim_id"],
        "fencing_token": 3,
        "scope": ["effect:execute"],
        "actions": [f"conformance.document:{material.action}"],
        "resources": [
            {
                "type": "conformance.document",
                "descriptor_version": "v1",
                "id": material.resource_id,
            }
        ],
        "credential_binding_ids": [],
        "cnf": {"jkt": material.proof_thumbprint},
        "effect_id": str(material.effect_id),
        "effect_key": f"document:{material.action}",
    }


def _public_jwk(key: ec.EllipticCurvePublicKey) -> dict[str, str]:
    numbers = key.public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": _coordinate(numbers.x),
        "y": _coordinate(numbers.y),
    }


def _coordinate(value: int) -> str:
    return base64.urlsafe_b64encode(value.to_bytes(32, "big")).rstrip(b"=").decode()


def _digest(value: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(value).digest()).rstrip(b"=").decode()


__all__ = ("CapabilityMaterial", "CapabilitySigner", "proof_thumbprint")
