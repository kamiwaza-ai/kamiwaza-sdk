"""Workload assertion sources and ephemeral DPoP proof-key lifecycle."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import NoReturn, Protocol, SupportsIndex
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import SecretStr

MAX_ASSERTION_BYTES = 65_536
_KUBERNETES_ASSERTION_PATH = Path(
    "/var/run/secrets/kamiwaza.ai/workload-identity/token"
)
_REDACTED = "sensitive delegated-workload values cannot be serialized"


class SensitiveValue(SecretStr):
    """A process-local string that redacts display and rejects pickling."""

    def __reduce__(self) -> NoReturn:
        raise TypeError(_REDACTED)

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError(_REDACTED)


class WorkloadAssertion(SensitiveValue):
    """Ambient workload assertion read only by a selected SDK adapter."""


class DelegatedCapability(SensitiveValue):
    """Sender-constrained capability held only in SDK process memory."""


class DPoPProof(SensitiveValue):
    """One exact-request DPoP proof held only until transport sends it."""


class DPoPNonce(SensitiveValue):
    """One server challenge used only for the bounded proof retry."""


class BrokerHandle(SensitiveValue):
    """Opaque credential-broker authority retained only by the SDK."""


class OneUseToken(SensitiveValue):
    """One-use protected-resource consumption authority."""


class CsrfToken(SensitiveValue):
    """Member-session CSRF material that must not enter representations."""


class AttestationProfile(str, Enum):
    """Portable v1 Kubernetes profiles that share the projected assertion."""

    KUBERNETES_OFFLINE_V1 = "kubernetes-offline-v1"
    KUBERNETES_TOKENREVIEW_V1 = "kubernetes-tokenreview-v1"


@dataclass(frozen=True, slots=True, kw_only=True)
class DPoPProofRequest:
    """One exact protocol request covered by a fresh proof."""

    method: str
    target_uri: str
    access_token: DelegatedCapability | str | None = field(default=None, repr=False)
    body_digest: str
    issued_at: datetime
    nonce: DPoPNonce | str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.access_token, str):
            object.__setattr__(
                self, "access_token", DelegatedCapability(self.access_token)
            )
        if isinstance(self.nonce, str):
            object.__setattr__(self, "nonce", DPoPNonce(self.nonce))


class DPoPKeyLifecycle:
    """One rotatable in-memory P-256 key that never serializes private material."""

    def __init__(self, private_key: ec.EllipticCurvePrivateKey) -> None:
        self._private_key: ec.EllipticCurvePrivateKey | None = private_key

    @classmethod
    def generate(cls) -> DPoPKeyLifecycle:
        return cls(ec.generate_private_key(ec.SECP256R1()))

    def create(self, request: DPoPProofRequest) -> DPoPProof:
        private_key = self._require_key()
        claims = _proof_claims(request)
        encoded = jwt.encode(
            claims,
            private_key,
            algorithm="ES256",
            headers={"typ": "dpop+jwt", "jwk": self.public_jwk()},
        )
        return DPoPProof(encoded)

    def public_jwk(self) -> dict[str, str]:
        """Return a fresh public-only representation of the active proof key."""
        return _public_jwk(self._require_key().public_key())

    def thumbprint(self) -> str:
        canonical = json.dumps(
            self.public_jwk(), separators=(",", ":"), sort_keys=True
        ).encode()
        return _base64url(hashlib.sha256(canonical).digest())

    def rotate(self) -> None:
        self._require_key()
        self._private_key = ec.generate_private_key(ec.SECP256R1())

    def close(self) -> None:
        self._private_key = None

    def _require_key(self) -> ec.EllipticCurvePrivateKey:
        private_key = self._private_key
        if private_key is None:
            _raise_proof_key_unavailable()
        return private_key

    def __enter__(self) -> DPoPKeyLifecycle:
        self._require_key()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __repr__(self) -> str:
        state = "closed" if self._private_key is None else "active"
        return f"DPoPKeyLifecycle(state={state!r}, private_key=<redacted>)"

    def __reduce__(self) -> NoReturn:
        raise TypeError(_REDACTED)

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError(_REDACTED)


class WorkloadAssertionAdapter(Protocol):
    """Trusted source for one selected workload-attestation profile."""

    @property
    def profile_id(self) -> str: ...

    def read(self) -> WorkloadAssertion: ...


@dataclass(frozen=True, slots=True)
class KubernetesProjectedAssertionAdapter:
    """Read the one fixed Kamiwaza projected-token path after local checks."""

    profile: AttestationProfile

    @property
    def profile_id(self) -> str:
        return self.profile.value

    def read(self) -> WorkloadAssertion:
        return WorkloadAssertion(_read_projected_assertion())


class AssertionAdapterRegistry:
    """Closed lookup for the adapters trusted by this SDK process."""

    def __init__(self, adapters: Iterable[WorkloadAssertionAdapter]) -> None:
        self._adapters: dict[str, WorkloadAssertionAdapter] = {}
        for adapter in adapters:
            if adapter.profile_id in self._adapters:
                _raise_unsupported_profile()
            self._adapters[adapter.profile_id] = adapter

    @classmethod
    def kubernetes_v1(cls) -> AssertionAdapterRegistry:
        return cls(
            KubernetesProjectedAssertionAdapter(profile) for profile in AttestationProfile
        )

    def selected(self, profile: AttestationProfile | str) -> WorkloadAssertionAdapter:
        profile_id = profile.value if isinstance(profile, AttestationProfile) else profile
        adapter = self._adapters.get(profile_id)
        if adapter is None:
            _raise_unsupported_profile()
        return adapter


class WorkloadProof:
    """Selected assertion adapter and proof key for one workload revision."""

    def __init__(
        self,
        key: DPoPKeyLifecycle,
        adapters: AssertionAdapterRegistry | None = None,
        selected_profile: AttestationProfile | str | None = None,
    ) -> None:
        self._key = key
        self._adapters = adapters
        self._selected_profile: str | None = None
        if selected_profile is not None:
            self._selected_profile = self._resolve_profile(selected_profile)

    @classmethod
    def proof_only(cls, key: DPoPKeyLifecycle | None = None) -> WorkloadProof:
        return cls(key or DPoPKeyLifecycle.generate())

    @classmethod
    def kubernetes(
        cls, selected_profile: AttestationProfile | str
    ) -> WorkloadProof:
        return cls(
            DPoPKeyLifecycle.generate(),
            AssertionAdapterRegistry.kubernetes_v1(),
            selected_profile,
        )

    def assertion(self) -> WorkloadAssertion:
        selected_profile = self._selected_profile
        adapters = self._adapters
        if selected_profile is None:
            _raise_workload_assertion_unavailable()
        if adapters is None:
            _raise_workload_assertion_unavailable()
        return adapters.selected(selected_profile).read()

    def create(self, request: DPoPProofRequest) -> DPoPProof:
        return self._key.create(request)

    def public_jwk(self) -> dict[str, str]:
        return self._key.public_jwk()

    def key_thumbprint(self) -> str:
        return self._key.thumbprint()

    def rotate_key(self) -> None:
        self._key.rotate()

    def select_profile(self, profile: AttestationProfile | str) -> None:
        selected = self._resolve_profile(profile)
        if selected == self._selected_profile:
            return
        self._key.rotate()
        self._selected_profile = selected

    def close(self) -> None:
        self._key.close()

    def _resolve_profile(self, profile: AttestationProfile | str) -> str:
        adapters = self._adapters
        if adapters is None:
            _raise_unsupported_profile()
        return adapters.selected(profile).profile_id

    def __repr__(self) -> str:
        return (
            "WorkloadProof("
            f"selected_profile={self._selected_profile!r}, private_key=<redacted>)"
        )


DPoPProofKey = DPoPKeyLifecycle


def body_digest(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _proof_claims(request: DPoPProofRequest) -> dict[str, object]:
    claims: dict[str, object] = {
        "htu": request.target_uri,
        "htm": request.method.upper(),
        "iat": int(request.issued_at.timestamp()),
        "jti": str(uuid4()),
        "body_sha256": request.body_digest,
    }
    if request.access_token is not None:
        claims["ath"] = _access_token_hash(_secret_value(request.access_token))
    if request.nonce is not None:
        claims["nonce"] = _secret_value(request.nonce)
    return claims


def _read_projected_assertion() -> str:
    try:
        descriptor = os.open(_KUBERNETES_ASSERTION_PATH, os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        _raise_workload_assertion_unavailable()
    try:
        metadata = os.fstat(descriptor)
        if not _secure_assertion_file(metadata):
            _raise_workload_assertion_unavailable()
        raw = os.read(descriptor, MAX_ASSERTION_BYTES + 1)
    except OSError:
        _raise_workload_assertion_unavailable()
    finally:
        os.close(descriptor)
    return _decode_assertion(raw)


def _secure_assertion_file(metadata: os.stat_result) -> bool:
    if not stat.S_ISREG(metadata.st_mode):
        return False
    if metadata.st_uid not in {0, os.geteuid()}:
        return False
    permissions = stat.S_IMODE(metadata.st_mode)
    forbidden = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH | stat.S_IXUSR
    forbidden |= stat.S_IXGRP | stat.S_IXOTH | stat.S_IROTH
    if permissions & forbidden:
        return False
    return _effective_identity_can_read(metadata, permissions)


def _effective_identity_can_read(metadata: os.stat_result, permissions: int) -> bool:
    if metadata.st_uid == os.geteuid():
        return bool(permissions & stat.S_IRUSR)
    groups = {os.getegid(), *os.getgroups()}
    return metadata.st_gid in groups and bool(permissions & stat.S_IRGRP)


def _decode_assertion(raw: bytes) -> str:
    if len(raw) > MAX_ASSERTION_BYTES:
        _raise_workload_assertion_unavailable()
    try:
        assertion = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        _raise_workload_assertion_unavailable()
    if not assertion:
        _raise_workload_assertion_unavailable()
    return assertion


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


def _secret_value(value: SensitiveValue | str) -> str:
    return value.get_secret_value() if isinstance(value, SensitiveValue) else value


def _raise_proof_key_unavailable() -> NoReturn:
    from kamiwaza_sdk.delegated_workloads.errors import ProofKeyUnavailable

    raise ProofKeyUnavailable() from None


def _raise_workload_assertion_unavailable() -> NoReturn:
    from kamiwaza_sdk.delegated_workloads.errors import WorkloadAssertionUnavailable

    raise WorkloadAssertionUnavailable() from None


def _raise_unsupported_profile() -> NoReturn:
    from kamiwaza_sdk.delegated_workloads.errors import UnsupportedAttestationProfile

    raise UnsupportedAttestationProfile() from None
