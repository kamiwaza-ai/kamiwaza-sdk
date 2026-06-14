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
