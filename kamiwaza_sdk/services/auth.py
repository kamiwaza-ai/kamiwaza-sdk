"""Auth service client for the Kamiwaza API surface."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional
from uuid import UUID

from .base_service import BaseService
from ..exceptions import APIError, AuthorizationError
from ..schemas.auth import (
    IdentityProviderListResponse,
    IdentityProviderOperationResponse,
    JWKSResponse,
    LocalUserCreateRequest,
    LocalUserPasswordChangeRequest,
    LocalUserPasswordResetRequest,
    LocalUserResponse,
    LocalUserUpdateRequest,
    LogoutResponse,
    PATCreate,
    PATCreateResponse,
    PATListResponse,
    PasswordChangeResponse,
    RegisterIdPRequest,
    SessionPurgeRequest,
    SessionPurgeResponse,
    ToggleIdPRequest,
    TokenResponse,
    UserInfo,
    ValidationHeaders,
)


class AuthService(BaseService):
    """High-level helpers for interacting with Kamiwaza authentication endpoints."""

    # --------------------------------------------------------------------- #
    # Session & token management
    # --------------------------------------------------------------------- #
    def login_with_password(
        self,
        username: str,
        password: str,
        *,
        client_id: str = "kamiwaza-platform",
        scope: str = "openid email profile",
        client_secret: Optional[str] = None,
    ) -> TokenResponse:
        """Password grant against the auth gateway (Keycloak-backed)."""
        form_data = {
            "grant_type": "password",
            "username": username,
            "password": password,
            "scope": scope,
            "client_id": client_id,
        }
        if client_secret:
            form_data["client_secret"] = client_secret
        response = self.client.post(
            "/auth/token",
            data=form_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            skip_auth=True,
        )
        return TokenResponse.model_validate(response)

    def refresh_access_token(self, refresh_token: str) -> TokenResponse:
        """Exchange a refresh token for a new access token."""
        response = self.client.post(
            "/auth/refresh",
            params={"refresh_token": refresh_token},
            skip_auth=True,
        )
        return TokenResponse.model_validate(response)

    def logout(self) -> LogoutResponse:
        """Invoke the coordinated logout flow."""
        response = self.client.post("/auth/logout", json={})
        return LogoutResponse.model_validate(response)

    def get_current_user(self) -> UserInfo:
        """Return the current subject claims from `/auth/users/me`."""
        response = self.client.get("/auth/users/me")
        return UserInfo.model_validate(response)

    def forward_auth_headers(self) -> ValidationHeaders:
        """Call `/auth/validate` and parse the response header contract."""
        raw_response = self.client.get("/auth/validate", expect_json=False)
        if raw_response.status_code != 200:
            raise APIError(
                f"ForwardAuth validation failed with status "
                f"{raw_response.status_code}: {raw_response.text}"
            )
        return ValidationHeaders.from_headers(raw_response.headers)

    def get_jwks(self) -> JWKSResponse:
        """Fetch the gateway JWKS set."""
        response = self.client.get("/auth/jwks")
        return JWKSResponse.model_validate(response)

    def health(self) -> Dict[str, Any]:
        """Retrieve auth service health metadata."""
        return self.client.get("/auth/health")

    def metadata(self) -> Dict[str, Any]:
        """Fetch auth service metadata from the root endpoint."""
        return self.client.get("/auth/")

    # --------------------------------------------------------------------- #
    # Personal access tokens
    # --------------------------------------------------------------------- #
    def create_pat(self, payload: PATCreate) -> PATCreateResponse:
        params = payload.model_dump(exclude_none=True)
        response = self.client.post("/auth/pats", params=params if params else None)
        return PATCreateResponse.model_validate(response)

    def list_pats(self) -> PATListResponse:
        response = self.client.get("/auth/pats")
        return PATListResponse.model_validate(response)

    def revoke_pat(self, jti: str) -> Dict[str, Any]:
        return self.client.delete(f"/auth/pats/{jti}")

    # --------------------------------------------------------------------- #
    # Session administration
    # --------------------------------------------------------------------- #
    def purge_sessions(self, payload: SessionPurgeRequest) -> SessionPurgeResponse:
        response = self.client.post(
            "/auth/sessions/purge",
            json=payload.model_dump(),
        )
        return SessionPurgeResponse.model_validate(response)

    def delete_session(self, session_id: str) -> Dict[str, Any]:
        return self.client.delete(f"/auth/sessions/{session_id}")

    # --------------------------------------------------------------------- #
    # Identity provider lifecycle
    # --------------------------------------------------------------------- #
    def list_identity_providers(self) -> IdentityProviderListResponse:
        response = self.client.get("/auth/idp/providers")
        return IdentityProviderListResponse.model_validate(response)

    def list_public_identity_providers(self) -> IdentityProviderListResponse:
        response = self.client.get("/auth/idp/public/providers")
        return IdentityProviderListResponse.model_validate(response)

    def register_identity_provider(
        self, payload: RegisterIdPRequest
    ) -> IdentityProviderOperationResponse:
        response = self.client.post(
            "/auth/idp/register",
            json=payload.model_dump(exclude_none=True),
        )
        return IdentityProviderOperationResponse.model_validate(response)

    def update_identity_provider(
        self,
        alias: str,
        payload: RegisterIdPRequest,
    ) -> IdentityProviderOperationResponse:
        response = self.client.put(
            f"/auth/idp/{alias}",
            json=payload.model_dump(exclude_none=True),
        )
        return IdentityProviderOperationResponse.model_validate(response)

    def toggle_identity_provider(
        self,
        alias: str,
        payload: ToggleIdPRequest,
    ) -> IdentityProviderOperationResponse:
        response = self.client.patch(
            f"/auth/idp/{alias}",
            json=payload.model_dump(),
        )
        return IdentityProviderOperationResponse.model_validate(response)

    def delete_identity_provider(self, alias: str) -> IdentityProviderOperationResponse:
        response = self.client.delete(f"/auth/idp/{alias}")
        return IdentityProviderOperationResponse.model_validate(response)

    # --------------------------------------------------------------------- #
    # Local user management
    # --------------------------------------------------------------------- #
    def list_users(self) -> list[LocalUserResponse]:
        response = self.client.get("/auth/users/")
        return [LocalUserResponse.model_validate(user) for user in response]

    def get_user(self, user_id: UUID) -> LocalUserResponse:
        response = self.client.get(f"/auth/users/{user_id}")
        return LocalUserResponse.model_validate(response)

    def create_local_user(self, payload: LocalUserCreateRequest) -> LocalUserResponse:
        response = self.client.post(
            "/auth/users/local",
            json=payload.model_dump(exclude_none=True),
        )
        return LocalUserResponse.model_validate(response)

    def update_user(self, user_id: UUID, payload: LocalUserUpdateRequest) -> LocalUserResponse:
        response = self.client.put(
            f"/auth/users/{user_id}",
            json=payload.model_dump(exclude_none=True),
        )
        return LocalUserResponse.model_validate(response)

    def delete_user(self, user_id: UUID) -> LocalUserResponse:
        response = self.client.delete(f"/auth/users/{user_id}")
        return LocalUserResponse.model_validate(response)

    def reset_user_password(
        self,
        user_id: UUID,
        payload: LocalUserPasswordResetRequest,
    ) -> LocalUserResponse:
        response = self.client.post(
            f"/auth/users/{user_id}/password",
            json=payload.model_dump(),
        )
        return LocalUserResponse.model_validate(response)

    def change_my_password(
        self,
        payload: LocalUserPasswordChangeRequest,
    ) -> PasswordChangeResponse:
        try:
            response = self.client.post(
                "/auth/users/me/password",
                json=payload.model_dump(),
            )
        except APIError as exc:
            if exc.status_code == 403:
                raise AuthorizationError(
                    "Password self-service is unavailable for SSO users; "
                    "use your identity provider to change credentials."
                ) from exc
            raise
        return PasswordChangeResponse.model_validate(response)

    @staticmethod
    def require_admin(headers: ValidationHeaders) -> None:
        """Ensure ForwardAuth headers include the admin role."""
        roles = {role.lower() for role in headers.user_roles}
        if "admin" not in roles:
            raise AuthorizationError("Admin role required for this operation.")
