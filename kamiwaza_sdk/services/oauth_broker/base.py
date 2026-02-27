"""OAuth Broker service client for the Kamiwaza API."""

from __future__ import annotations

from uuid import UUID

from ..base_service import BaseService
from ...schemas.oauth_broker import (
    AppInstallationCreate,
    AppInstallationListResponse,
    AppInstallationResponse,
    AppInstallationUpdate,
    ConnectionResponse,
    ConnectionStatusResponse,
    GoogleAuthStartResponse,
    Provider,
)
from .policy import PolicyMixin
from .proxy import ProxyMixin
from .token import TokenMixin


class OAuthBrokerService(BaseService, ProxyMixin, TokenMixin, PolicyMixin):
    """High-level client for interacting with Kamiwaza OAuth Broker."""

    # ========== App Installation Management ==========

    def create_app_installation(
        self, app: AppInstallationCreate
    ) -> AppInstallationResponse:
        """
        Create a new app installation.

        Args:
            app: App installation details

        Returns:
            Created app installation

        Example:
            >>> app = AppInstallationCreate(
            ...     name="Email Assistant",
            ...     description="AI email helper",
            ...     allowed_tools=["gmail-reader", "gmail-sender"]
            ... )
            >>> installation = client.oauth_broker.create_app_installation(app)
        """
        response = self.client.post(
            "/oauth-broker/apps", json=app.model_dump(exclude_none=True)
        )
        return AppInstallationResponse.model_validate(response)

    def list_app_installations(
        self, limit: int = 100, offset: int = 0
    ) -> AppInstallationListResponse:
        """
        List app installations for the authenticated user.

        Args:
            limit: Maximum number of results
            offset: Pagination offset

        Returns:
            List of app installations
        """
        response = self.client.get(
            "/oauth-broker/apps", params={"limit": limit, "offset": offset}
        )
        return AppInstallationListResponse.model_validate(response)

    def get_app_installation(self, app_id: UUID) -> AppInstallationResponse:
        """
        Get a specific app installation.

        Args:
            app_id: App installation ID

        Returns:
            App installation details
        """
        response = self.client.get(f"/oauth-broker/apps/{app_id}")
        return AppInstallationResponse.model_validate(response)

    def update_app_installation(
        self, app_id: UUID, update: AppInstallationUpdate
    ) -> AppInstallationResponse:
        """
        Update an app installation.

        Args:
            app_id: App installation ID
            update: Fields to update

        Returns:
            Updated app installation
        """
        response = self.client.patch(
            f"/oauth-broker/apps/{app_id}",
            json=update.model_dump(exclude_none=True),
        )
        return AppInstallationResponse.model_validate(response)

    def delete_app_installation(self, app_id: UUID) -> None:
        """
        Delete an app installation.

        Args:
            app_id: App installation ID
        """
        self.client.delete(f"/oauth-broker/apps/{app_id}")

    # ========== OAuth Authentication Flow ==========

    def start_google_auth(
        self, app_id: UUID, scopes: list[str]
    ) -> GoogleAuthStartResponse:
        """
        Start Google OAuth authorization code flow.

        Args:
            app_id: App installation ID
            scopes: List of Google OAuth scopes to request

        Returns:
            Authorization URL and state parameter

        Example:
            >>> scopes = [
            ...     "https://www.googleapis.com/auth/gmail.readonly",
            ...     "https://www.googleapis.com/auth/gmail.compose"
            ... ]
            >>> result = client.oauth_broker.start_google_auth(app_id, scopes)
            >>> print(result.auth_url)  # Redirect user to this URL
        """
        response = self.client.get(
            "/oauth-broker/auth/google/start",
            params={"app_id": str(app_id), "scopes": " ".join(scopes)},
        )
        return GoogleAuthStartResponse.model_validate(response)

    def handle_google_callback(
        self, code: str, state: str, scope: str | None = None
    ) -> ConnectionResponse:
        """
        Handle Google OAuth callback.

        Args:
            code: Authorization code from Google
            state: State parameter for CSRF validation
            scope: Granted scopes (optional)

        Returns:
            Created connection

        Note:
            This is typically called automatically by the OAuth redirect handler.
            OAuth callback arrives as GET redirect from provider.
        """
        # OAuth callback arrives as GET redirect from provider
        response = self.client.get(
            "/oauth-broker/auth/google/callback",
            params={
                "code": code,
                "state": state,
                **({"scope": scope} if scope else {}),
            },
        )
        return ConnectionResponse.model_validate(response)

    # ========== Connection Management ==========

    def get_connection_status(
        self, app_id: UUID, provider: Provider | str
    ) -> ConnectionStatusResponse:
        """
        Get connection status for user.

        Args:
            app_id: App installation ID
            provider: OAuth provider (e.g., "google")

        Returns:
            Connection status (connected, needs_reauth, or disconnected)

        Example:
            >>> status = client.oauth_broker.get_connection_status(app_id, "google")
            >>> if status.status == "connected":
            ...     print(f"Connected as {status.external_email}")
        """
        provider_str = provider.value if isinstance(provider, Provider) else provider
        response = self.client.get(
            "/oauth-broker/connections/status",
            params={"app_id": str(app_id), "provider": provider_str},
        )
        return ConnectionStatusResponse.model_validate(response)

    def disconnect(self, app_id: UUID, provider: Provider | str) -> None:
        """
        Disconnect user from provider.

        This will revoke tokens with the provider and delete the connection.

        Args:
            app_id: App installation ID
            provider: OAuth provider

        Example:
            >>> client.oauth_broker.disconnect(app_id, "google")
        """
        provider_str = provider.value if isinstance(provider, Provider) else provider
        self.client.delete(
            "/oauth-broker/connections/disconnect",
            params={"app_id": str(app_id), "provider": provider_str},
        )
