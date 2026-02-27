"""OAuth Broker service client for the Kamiwaza API."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from .base_service import BaseService
from ..schemas.oauth_broker import (
    AppInstallationCreate,
    AppInstallationListResponse,
    AppInstallationResponse,
    AppInstallationUpdate,
    ConnectionResponse,
    ConnectionStatusResponse,
    DriveListFilesRequest,
    GmailGetMessageRequest,
    GmailModifyRequest,
    GmailSearchRequest,
    GmailSendRequest,
    GoogleAuthStartResponse,
    LeaseStatusResponse,
    MintTokenRequest,
    MintTokenResponse,
    Provider,
    ToolPolicyCreate,
    ToolPolicyListResponse,
    ToolPolicyResponse,
    ToolPolicyUpdate,
)


class OAuthBrokerService(BaseService):
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
            params={"app_id": str(app_id), "scopes": ",".join(scopes)},
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
        """
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

    # ========== Ephemeral Token Minting (Mode 2) ==========

    def mint_ephemeral_token(
        self, request: MintTokenRequest
    ) -> MintTokenResponse:
        """
        Mint an ephemeral access token (Mode 2 - Advanced).

        **WARNING**: Only use in high-security environments with restricted network egress.
        For most use cases, use Proxy Mode (Mode 1) instead.

        Args:
            request: Token mint request

        Returns:
            Ephemeral access token with lease tracking

        Example:
            >>> request = MintTokenRequest(
            ...     app_installation_id=app_id,
            ...     tool_id="gmail-reader",
            ...     provider="google",
            ...     lease_duration=300
            ... )
            >>> token = client.oauth_broker.mint_ephemeral_token(request)
            >>> # Use token.access_token with provider API
        """
        response = self.client.post(
            "/oauth-broker/tokens/mint", json=request.model_dump(exclude_none=True)
        )
        return MintTokenResponse.model_validate(response)

    def get_lease_status(self, lease_id: str) -> LeaseStatusResponse:
        """
        Get status of a token lease.

        Args:
            lease_id: Lease identifier from mint response

        Returns:
            Lease status including expiry and validity
        """
        response = self.client.get(f"/oauth-broker/tokens/leases/{lease_id}")
        return LeaseStatusResponse.model_validate(response)

    def revoke_lease(self, lease_id: str) -> None:
        """
        Revoke a token lease early.

        Note: This invalidates the lease tracking but does NOT revoke
        the token with the provider.

        Args:
            lease_id: Lease identifier
        """
        self.client.delete(f"/oauth-broker/tokens/leases/{lease_id}")

    # ========== Tool Policy Management ==========

    def create_tool_policy(self, policy: ToolPolicyCreate) -> ToolPolicyResponse:
        """
        Create a new tool policy.

        Args:
            policy: Tool policy details

        Returns:
            Created tool policy

        Example:
            >>> policy = ToolPolicyCreate(
            ...     app_installation_id=app_id,
            ...     tool_id="gmail-reader",
            ...     provider="google",
            ...     allowed_operations=["gmail.search", "gmail.getMessage"],
            ...     allowed_scope_subset=["https://www.googleapis.com/auth/gmail.readonly"]
            ... )
            >>> created = client.oauth_broker.create_tool_policy(policy)
        """
        response = self.client.post(
            "/oauth-broker/tool-policies", json=policy.model_dump(exclude_none=True)
        )
        return ToolPolicyResponse.model_validate(response)

    def list_tool_policies(
        self, app_id: UUID | None = None, limit: int = 100, offset: int = 0
    ) -> ToolPolicyListResponse:
        """
        List tool policies.

        Args:
            app_id: Optional app installation ID to filter by
            limit: Maximum number of results
            offset: Pagination offset

        Returns:
            List of tool policies
        """
        params: dict[str, int | str] = {"limit": limit, "offset": offset}
        if app_id:
            params["app_id"] = str(app_id)

        response = self.client.get("/oauth-broker/tool-policies", params=params)
        return ToolPolicyListResponse.model_validate(response)

    def get_tool_policy(self, policy_id: UUID) -> ToolPolicyResponse:
        """
        Get a specific tool policy.

        Args:
            policy_id: Tool policy ID

        Returns:
            Tool policy details
        """
        response = self.client.get(f"/oauth-broker/tool-policies/{policy_id}")
        return ToolPolicyResponse.model_validate(response)

    def update_tool_policy(
        self, policy_id: UUID, update: ToolPolicyUpdate
    ) -> ToolPolicyResponse:
        """
        Update a tool policy.

        Args:
            policy_id: Tool policy ID
            update: Fields to update

        Returns:
            Updated tool policy
        """
        response = self.client.patch(
            f"/oauth-broker/tool-policies/{policy_id}",
            json=update.model_dump(exclude_none=True),
        )
        return ToolPolicyResponse.model_validate(response)

    def delete_tool_policy(self, policy_id: UUID) -> None:
        """
        Delete a tool policy.

        Args:
            policy_id: Tool policy ID
        """
        self.client.delete(f"/oauth-broker/tool-policies/{policy_id}")

    # ========== Gmail Proxy Endpoints (Mode 1 - Recommended) ==========

    def gmail_search(
        self, app_id: UUID, tool_id: str, query: str, max_results: int = 10
    ) -> dict[str, Any]:
        """
        Proxy Gmail search request.

        Args:
            app_id: App installation ID
            tool_id: Tool identifier
            query: Gmail search query (e.g., "is:unread", "from:user@example.com")
            max_results: Maximum number of results

        Returns:
            Gmail API response

        Example:
            >>> results = client.oauth_broker.gmail_search(
            ...     app_id=app_id,
            ...     tool_id="gmail-reader",
            ...     query="is:unread subject:report",
            ...     max_results=20
            ... )
        """
        request = GmailSearchRequest(query=query, max_results=max_results)
        response = self.client.post(
            "/oauth-broker/proxy/google/gmail/search",
            json=request.model_dump(),
            params={"app_id": str(app_id), "tool_id": tool_id},
        )
        return response

    def gmail_get_message(
        self, app_id: UUID, tool_id: str, message_id: str, format: str = "full"
    ) -> dict[str, Any]:
        """
        Proxy Gmail get message request.

        Args:
            app_id: App installation ID
            tool_id: Tool identifier
            message_id: Gmail message ID
            format: Message format (full, metadata, minimal, raw)

        Returns:
            Gmail API response
        """
        request = GmailGetMessageRequest(message_id=message_id, format=format)
        response = self.client.post(
            "/oauth-broker/proxy/google/gmail/getMessage",
            json=request.model_dump(),
            params={"app_id": str(app_id), "tool_id": tool_id},
        )
        return response

    def gmail_send(
        self, app_id: UUID, tool_id: str, raw_message: str
    ) -> dict[str, Any]:
        """
        Proxy Gmail send request.

        Args:
            app_id: App installation ID
            tool_id: Tool identifier
            raw_message: Base64url encoded RFC 2822 message

        Returns:
            Gmail API response
        """
        request = GmailSendRequest(raw_message=raw_message)
        response = self.client.post(
            "/oauth-broker/proxy/google/gmail/send",
            json=request.model_dump(),
            params={"app_id": str(app_id), "tool_id": tool_id},
        )
        return response

    def gmail_list_labels(self, app_id: UUID, tool_id: str) -> dict[str, Any]:
        """
        Proxy Gmail list labels request.

        Args:
            app_id: App installation ID
            tool_id: Tool identifier

        Returns:
            Gmail API response
        """
        response = self.client.get(
            "/oauth-broker/proxy/google/gmail/labels",
            params={"app_id": str(app_id), "tool_id": tool_id},
        )
        return response

    def gmail_modify(
        self,
        app_id: UUID,
        tool_id: str,
        message_id: str,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Proxy Gmail modify message request.

        Used for operations like:
        - Mark as read: remove_labels=["UNREAD"]
        - Mark as unread: add_labels=["UNREAD"]
        - Delete (move to trash): add_labels=["TRASH"], remove_labels=["INBOX"]
        - Archive: remove_labels=["INBOX"]

        Args:
            app_id: App installation ID
            tool_id: Tool identifier
            message_id: Gmail message ID
            add_labels: Labels to add
            remove_labels: Labels to remove

        Returns:
            Gmail API response
        """
        request = GmailModifyRequest(
            message_id=message_id, add_labels=add_labels, remove_labels=remove_labels
        )
        response = self.client.post(
            "/oauth-broker/proxy/google/gmail/modify",
            json=request.model_dump(exclude_none=True),
            params={"app_id": str(app_id), "tool_id": tool_id},
        )
        return response

    # ========== Google Drive Proxy Endpoints ==========

    def drive_list_files(
        self, app_id: UUID, tool_id: str, query: str | None = None, page_size: int = 10
    ) -> dict[str, Any]:
        """
        Proxy Google Drive list files request.

        Args:
            app_id: App installation ID
            tool_id: Tool identifier
            query: Drive query (e.g., "name contains 'report'")
            page_size: Maximum number of files

        Returns:
            Drive API response

        Example:
            >>> files = client.oauth_broker.drive_list_files(
            ...     app_id=app_id,
            ...     tool_id="drive-reader",
            ...     query="mimeType='application/pdf'",
            ...     page_size=20
            ... )
        """
        request = DriveListFilesRequest(query=query, page_size=page_size)
        response = self.client.post(
            "/oauth-broker/proxy/google/drive/listFiles",
            json=request.model_dump(exclude_none=True),
            params={"app_id": str(app_id), "tool_id": tool_id},
        )
        return response

    def drive_get_file(
        self, app_id: UUID, tool_id: str, file_id: str
    ) -> dict[str, Any]:
        """
        Proxy Google Drive get file metadata request.

        Args:
            app_id: App installation ID
            tool_id: Tool identifier
            file_id: Drive file ID

        Returns:
            Drive API response
        """
        response = self.client.get(
            f"/oauth-broker/proxy/google/drive/files/{file_id}",
            params={"app_id": str(app_id), "tool_id": tool_id},
        )
        return response

    # ========== Google Calendar Proxy Endpoints ==========

    def calendar_list_calendars(self, app_id: UUID, tool_id: str) -> dict[str, Any]:
        """
        Proxy Google Calendar list calendars request.

        Args:
            app_id: App installation ID
            tool_id: Tool identifier

        Returns:
            Calendar API response
        """
        response = self.client.get(
            "/oauth-broker/proxy/google/calendar/calendars",
            params={"app_id": str(app_id), "tool_id": tool_id},
        )
        return response

    def calendar_list_events(
        self,
        app_id: UUID,
        tool_id: str,
        calendar_id: str = "primary",
        time_min: str | None = None,
        time_max: str | None = None,
        max_results: int = 10,
    ) -> dict[str, Any]:
        """
        Proxy Google Calendar list events request.

        Args:
            app_id: App installation ID
            tool_id: Tool identifier
            calendar_id: Calendar ID (default: "primary")
            time_min: RFC3339 timestamp for start time
            time_max: RFC3339 timestamp for end time
            max_results: Maximum number of events

        Returns:
            Calendar API response

        Example:
            >>> events = client.oauth_broker.calendar_list_events(
            ...     app_id=app_id,
            ...     tool_id="calendar-reader",
            ...     time_min="2026-02-12T00:00:00Z",
            ...     time_max="2026-02-13T00:00:00Z",
            ...     max_results=50
            ... )
        """
        params = {
            "app_id": str(app_id),
            "tool_id": tool_id,
            "calendar_id": calendar_id,
            "max_results": max_results,
        }
        if time_min:
            params["time_min"] = time_min
        if time_max:
            params["time_max"] = time_max

        response = self.client.get(
            "/oauth-broker/proxy/google/calendar/events", params=params
        )
        return response
