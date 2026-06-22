# kamiwaza_sdk/schemas/connectors.py

"""Pydantic models for the external-connectors API (M365, Google, …).

This covers the cluster-wide connector *app registration* (admin-scoped): the
tenant/client identifiers an operator registers once per cluster. The per-user
OAuth connection (Device Code Flow) is a separate, interactive flow and is not
modeled here — users connect themselves.
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Default M365 Graph scopes, matching the platform connector UI defaults.
M365_DEFAULT_SCOPES: List[str] = [
    "User.Read",
    "Files.ReadWrite.All",
    "Sites.ReadWrite.All",
    "Mail.ReadWrite",
    "Calendars.ReadWrite",
]


class M365ConnectorConfig(BaseModel):
    """Cluster-wide M365 app-registration config (Device Code Flow, public client).

    Both fields are public Azure AD identifiers — not secrets. No client_secret
    is used (Device Code Flow is a public-client flow).
    """

    model_config = ConfigDict(extra="allow")

    tenant_id: str = Field(..., min_length=1, description="Azure AD tenant ID")
    client_id: str = Field(..., min_length=1, description="App registration client ID")


class ExternalConnectorCreate(BaseModel):
    """Request to register a cluster-wide connector (admin-scoped)."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(..., min_length=1, max_length=255)
    connector_type: Literal["m365", "google", "confluence"]
    config: Dict[str, Any] = Field(..., description="Provider-specific configuration")
    scopes: List[str] = Field(..., min_length=1, description="OAuth scopes")
    enabled: bool = True


class ExternalConnector(BaseModel):
    """Connector response (sensitive config is never echoed back)."""

    model_config = ConfigDict(extra="allow")

    id: UUID
    name: str
    connector_type: str
    enabled: bool
    scopes: List[str] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    redirect_uri: Optional[str] = None


class ConnectorSubscriptionCreate(BaseModel):
    """Subscribe an out-of-core connector by its manifest + endpoint.

    The ``manifest`` is the connector's self-describing ``ConnectorSpec`` rendered
    via ``to_manifest()`` (``connector_type`` + identity + ``auth_model.kind``);
    ``endpoint`` is the HTTP/MCP URL the platform reaches it on. No secret values
    appear here — the platform brokers credentials, they are not carried inline.
    """

    model_config = ConfigDict(extra="allow")

    manifest: Dict[str, Any] = Field(
        ...,
        description="The connector's self-describing manifest (ConnectorSpec.to_manifest()).",
    )
    endpoint: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="HTTP/MCP endpoint where the connector is reached.",
    )
    config: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Provider-specific secret config (e.g. a service token), stored "
            "encrypted by the platform. Empty when no credential is needed."
        ),
    )
    workload_principal_id: Optional[str] = Field(
        default=None,
        description=(
            "Service-account principal allowed to mint per-user tokens for this "
            "connector out-of-core. Bound at install; the platform compares it "
            "against the caller's principal at mint time. None for connectors "
            "that never mint out-of-core."
        ),
    )


class ExternalConnectorUpdate(BaseModel):
    """Partial update for a registered connector; only set fields are sent."""

    model_config = ConfigDict(extra="allow")

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    config: Optional[Dict[str, Any]] = Field(default=None)
    scopes: Optional[List[str]] = Field(default=None, min_length=1)
    enabled: Optional[bool] = Field(default=None)


class AvailableConnector(BaseModel):
    """User-safe connector metadata for account-connection flows.

    Mirrors an item from the platform's ``/connectors/available`` endpoint: only
    enabled connectors, with non-sensitive fields (no timestamps or redirect URI).
    """

    model_config = ConfigDict(extra="allow")

    id: UUID
    name: str
    connector_type: str
    enabled: bool
    scopes: List[str] = []
