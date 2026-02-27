"""Pydantic schemas for OAuth Broker service."""

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ========== Provider and Status Enums ==========


class Provider(str, Enum):
    """Supported OAuth providers."""

    GOOGLE = "google"
    MICROSOFT = "microsoft"


class ConnectionStatus(str, Enum):
    """Connection status values."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    NEEDS_REAUTH = "needs_reauth"
    REVOKED = "revoked"


# ========== App Installation Schemas ==========


class AppInstallationCreate(BaseModel):
    """Schema for creating a new app installation."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255, description="App name")
    description: str | None = Field(
        default=None, max_length=1000, description="App description"
    )
    allowed_tools: list[str] = Field(
        default_factory=list,
        description="List of tool IDs allowed to use this app's connections",
    )
    app_metadata: dict | None = Field(default=None, description="Optional app metadata")


class AppInstallationUpdate(BaseModel):
    """Schema for updating an app installation."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    allowed_tools: list[str] | None = Field(default=None)
    lifecycle_status: str | None = Field(
        default=None, description="active, disabled, or deleted"
    )
    app_metadata: dict | None = Field(default=None)


class AppInstallationResponse(BaseModel):
    """Schema for app installation response."""

    model_config = ConfigDict(from_attributes=True, extra="allow")

    id: UUID
    name: str
    description: str | None = None
    owner_user_id: UUID
    lifecycle_status: str
    allowed_tools: list[str]
    app_metadata: dict | None = None
    created_at: datetime
    updated_at: datetime | None = None
    deleted_at: datetime | None = None


class AppInstallationListResponse(BaseModel):
    """Schema for listing app installations."""

    items: list[AppInstallationResponse]
    total: int = Field(..., description="Total number of app installations")


# ========== Connection Schemas ==========


class ConnectionResponse(BaseModel):
    """Schema for connection response."""

    model_config = ConfigDict(from_attributes=True, extra="allow")

    id: UUID
    app_installation_id: UUID
    user_id: UUID
    provider: Provider
    external_user_id: str | None = None
    external_email: str | None = None
    granted_scopes: list[str]
    expires_at: datetime
    status: ConnectionStatus
    created_at: datetime
    updated_at: datetime | None = None
    last_used_at: datetime | None = None
    last_refreshed_at: datetime | None = None


class ConnectionStatusResponse(BaseModel):
    """Response for connection status check."""

    model_config = ConfigDict(extra="allow")

    status: ConnectionStatus
    provider: Provider
    external_email: str | None = None
    granted_scopes: list[str] | None = None
    expires_at: datetime | None = None
    connected_at: datetime | None = None
    message: str | None = None


# ========== Ephemeral Token Schemas ==========


class MintTokenRequest(BaseModel):
    """Request to mint an ephemeral access token."""

    model_config = ConfigDict(extra="forbid")

    app_installation_id: UUID = Field(..., description="App installation ID")
    tool_id: str = Field(..., description="Tool identifier requesting token")
    provider: str = Field(..., description="Provider (google, microsoft)")
    scope_subset: list[str] | None = Field(
        None,
        description="Optional scope subset (must be subset of connection's granted_scopes)",
    )
    lease_duration: int = Field(
        300,
        ge=60,
        le=900,
        description="Lease duration in seconds (1-15 minutes, default 5 minutes)",
    )


class MintTokenResponse(BaseModel):
    """Response containing ephemeral access token."""

    model_config = ConfigDict(from_attributes=True, extra="allow")

    access_token: str = Field(
        ..., description="Provider access token (use directly with provider API)"
    )
    lease_id: str = Field(..., description="Lease identifier for tracking/revocation")
    expires_in: int = Field(..., description="Token expiry in seconds (from provider)")
    broker_lease_expires_in: int = Field(
        ..., description="Broker lease expiry in seconds"
    )
    token_type: str = Field(default="Bearer", description="Token type")
    granted_scopes: list[str] = Field(..., description="Actual granted scopes")


class LeaseStatusResponse(BaseModel):
    """Status of a token lease."""

    model_config = ConfigDict(from_attributes=True, extra="allow")

    lease_id: str
    app_installation_id: UUID
    tool_id: str
    provider: str
    granted_scopes: list[str]
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    is_valid: bool = Field(..., description="Whether lease is currently valid")


# ========== Google OAuth Schemas ==========


class GoogleAuthStartResponse(BaseModel):
    """Response when initiating Google OAuth flow."""

    auth_url: str = Field(
        ..., description="URL to redirect user to for Google authorization"
    )
    state: str = Field(..., description="CSRF protection state parameter")
    provider: str = Field(default="google", description="Provider name")


class GoogleCallbackRequest(BaseModel):
    """Request parameters from Google OAuth callback."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., description="Authorization code from Google")
    state: str = Field(..., description="State parameter for CSRF validation")
    scope: str | None = Field(
        default=None, description="Space-separated list of granted scopes"
    )


# ========== Tool Policy Schemas ==========


class ToolPolicyCreate(BaseModel):
    """Schema for creating a new tool policy."""

    model_config = ConfigDict(extra="forbid")

    app_installation_id: UUID = Field(..., description="App installation ID")
    tool_id: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Tool identifier (e.g., gmail-read-tool)",
    )
    provider: str = Field(..., description="Provider (google, microsoft, etc.)")
    allowed_operations: list[str] = Field(
        default_factory=list,
        description="Allowed operations (e.g., ['gmail.search', 'gmail.getMessage'])",
    )
    allowed_scope_subset: list[str] = Field(
        default_factory=list,
        description="Allowed OAuth scopes (must be subset of connection scopes)",
    )
    policy_metadata: dict | None = Field(
        default=None, description="Optional policy metadata"
    )


class ToolPolicyUpdate(BaseModel):
    """Schema for updating a tool policy."""

    model_config = ConfigDict(extra="forbid")

    allowed_operations: list[str] | None = Field(default=None)
    allowed_scope_subset: list[str] | None = Field(default=None)
    policy_metadata: dict | None = Field(default=None)


class ToolPolicyResponse(BaseModel):
    """Schema for tool policy response."""

    model_config = ConfigDict(from_attributes=True, extra="allow")

    id: UUID
    app_installation_id: UUID
    tool_id: str
    provider: str
    allowed_operations: list[str]
    allowed_scope_subset: list[str]
    policy_metadata: dict | None = None
    created_at: datetime
    updated_at: datetime | None = None


class ToolPolicyListResponse(BaseModel):
    """Schema for listing tool policies."""

    items: list[ToolPolicyResponse]
    total: int = Field(..., description="Total number of policies")


# ========== Proxy Request/Response Schemas ==========


class GmailSearchRequest(BaseModel):
    """Request body for Gmail search."""

    query: str
    max_results: int = Field(default=10, ge=1, le=200)


class GmailGetMessageRequest(BaseModel):
    """Request body for Gmail get message."""

    message_id: str
    msg_format: Literal["full", "metadata", "minimal", "raw"] = "full"


class GmailSendRequest(BaseModel):
    """Request body for Gmail send."""

    raw_message: str  # Base64url encoded RFC 2822 message


class GmailModifyRequest(BaseModel):
    """Request body for Gmail modify message."""

    message_id: str
    add_labels: list[str] | None = None
    remove_labels: list[str] | None = None


class DriveListFilesRequest(BaseModel):
    """Request body for Drive list files."""

    query: str | None = None
    page_size: int = Field(default=10, ge=1, le=200)
