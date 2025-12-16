"""Pydantic models for the authentication API surface."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


class TokenResponse(BaseModel):
    """Access/refresh token pair returned by the auth gateway."""

    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field(default="bearer", description="Token type (bearer)")
    expires_in: int = Field(..., description="Access token TTL in seconds")
    refresh_token: Optional[str] = Field(
        default=None, description="Refresh token used for silent renewal"
    )
    id_token: Optional[str] = Field(
        default=None, description="OpenID Connect ID token (when available)"
    )


class UserInfo(BaseModel):
    """Who-am-I payload normalised by the auth gateway."""

    username: str
    email: Optional[str] = None
    groups: List[str] = Field(default_factory=list)
    roles: List[str] = Field(default_factory=list)
    sub: str


class ValidationHeaders(BaseModel):
    """Structured representation of ForwardAuth validation headers."""

    user_id: Optional[str] = Field(default=None, alias="x-user-id")
    user_email: Optional[str] = Field(default=None, alias="x-user-email")
    user_name: Optional[str] = Field(default=None, alias="x-user-name")
    user_roles: List[str] = Field(default_factory=list, alias="x-user-roles")
    auth_token: Optional[str] = Field(default=None, alias="x-auth-token")
    request_id: Optional[str] = Field(default=None, alias="x-request-id")
    signature: Optional[str] = Field(default=None, alias="x-user-signature")
    signature_ts: Optional[str] = Field(default=None, alias="x-user-signature-ts")

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> "ValidationHeaders":
        # Normalise header keys to lower-case for lookup
        lowered = {key.lower(): value for key, value in headers.items()}
        roles_raw = lowered.get("x-user-roles", "") or ""
        roles = [role.strip() for role in roles_raw.split(",") if role.strip()]
        data: Dict[str, Any] = {
            "x-user-id": lowered.get("x-user-id"),
            "x-user-email": lowered.get("x-user-email"),
            "x-user-name": lowered.get("x-user-name"),
            "x-user-roles": roles,
            "x-auth-token": lowered.get("x-auth-token"),
            "x-request-id": lowered.get("x-request-id"),
            "x-user-signature": lowered.get("x-user-signature"),
            "x-user-signature-ts": lowered.get("x-user-signature-ts"),
        }
        return cls.model_validate(data)


class PATCreate(BaseModel):
    name: Optional[str] = None
    ttl_seconds: Optional[int] = Field(
        default=None, description="TTL in seconds for the PAT"
    )
    scope: Optional[str] = None
    aud: Optional[str] = None


class PAT(PATCreate):
    id: UUID
    jti: str
    owner_id: str
    exp: Optional[int] = None
    tenant_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    revoked: bool = Field(default=False)


class PATListResponse(BaseModel):
    pats: List[PAT]


class PATCreateResponse(BaseModel):
    token: str
    pat: PAT


def _default_scopes() -> List[str]:
    return ["openid", "profile", "email"]


class GoogleConfig(BaseModel):
    alias: str = Field(default="google")
    client_id: str
    client_secret: str
    hosted_domain: Optional[str] = None
    scopes: List[str] = Field(default_factory=_default_scopes)


class OIDCConfig(BaseModel):
    alias: str
    issuer: HttpUrl
    client_id: str
    client_secret: str
    scopes: List[str] = Field(default_factory=_default_scopes)


class RegisterIdPRequest(BaseModel):
    provider: str = Field(description="'google' or 'oidc'")
    google: Optional[GoogleConfig] = None
    oidc: Optional[OIDCConfig] = None
    ensure_redirects: bool = True


class ToggleIdPRequest(BaseModel):
    enabled: bool


class IdentityProvider(BaseModel):
    alias: str
    provider_id: Optional[str] = None
    display_name: Optional[str] = None
    enabled: bool = True


class IdentityProviderListResponse(BaseModel):
    providers: List[IdentityProvider] = Field(default_factory=list)
    idp_management_enabled: bool = True


class IdentityProviderOperationResponse(BaseModel):
    status: Optional[str] = None
    provider: Optional[IdentityProvider] = None
    idp_management_enabled: Optional[bool] = None
    deleted: Optional[bool] = None
    alias: Optional[str] = None


class LocalUserResponse(BaseModel):
    id: UUID
    username: str
    email: Optional[str] = None
    roles: List[str] = Field(default_factory=list)
    active: bool
    deleted: bool
    is_external: bool
    external_id: Optional[str] = None
    full_name: Optional[str] = None
    name: Optional[str] = None
    is_superuser: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class LocalUserCreateRequest(BaseModel):
    username: str
    email: Optional[str] = None
    password: str
    roles: Optional[List[str]] = None


class LocalUserUpdateRequest(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    active: Optional[bool] = None
    roles: Optional[List[str]] = None


class LocalUserPasswordResetRequest(BaseModel):
    new_password: str


class LocalUserPasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class PasswordChangeResponse(BaseModel):
    changed: bool


class SessionPurgeRequest(BaseModel):
    tenant_id: str
    subject_namespace: str = Field(default="user")
    subject_id: str


class SessionPurgeResponse(BaseModel):
    revoked: int


class LogoutResponse(BaseModel):
    message: str
    logout_time: str
    session_termination_requested: bool = False
    front_channel_logout_url: Optional[str] = None
    post_logout_redirect_uri: Optional[str] = None


class JWKSKey(BaseModel):
    alg: str
    kty: str
    use: str
    kid: str
    n: str
    e: str


class JWKSResponse(BaseModel):
    keys: List[JWKSKey]
