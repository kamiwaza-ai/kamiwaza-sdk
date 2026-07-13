# kamiwaza_sdk/schemas/connectors.py

"""Pydantic models for the connectors API.

This covers cluster-wide connector *registration* (admin-scoped): the manifest
+ config an operator registers once per cluster. The client is connector-agnostic
— ``config`` is an opaque dict validated server-side against the connector's
published manifest config_schema, so no connector type is modeled here. The
per-user OAuth connection is a separate, interactive flow and is not modeled here.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConnectorCreate(BaseModel):
    """Request to register a cluster-wide connector (admin-scoped).

    ``connector_type`` is an open string — the platform resolves it against the
    published catalog rather than a fixed enum, so new connector types need no
    SDK change.
    """

    model_config = ConfigDict(extra="allow")

    name: str = Field(..., min_length=1, max_length=255)
    connector_type: str = Field(..., min_length=1)
    config: Dict[str, Any] = Field(..., description="Provider-specific configuration")
    # Empty is valid: a service-token / catalog connector mints via its service
    # token rather than OAuth scopes, so it legitimately needs zero scopes. The
    # response and subscription models already treat [] as valid, so this stays
    # consistent with them.
    scopes: List[str] = Field(default_factory=list, description="OAuth scopes")
    enabled: bool = True


class Connector(BaseModel):
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
    scopes: List[str] = Field(
        default_factory=list,
        description=(
            "Scopes this connector is granted; the platform enforces requested "
            "scope_subset ⊆ these. Needed for service_token connectors to mint."
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


class ConnectorUpdate(BaseModel):
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

    The user-safe guarantee is enforced server-side by the endpoint, not by this
    model: ``extra="allow"`` means any additional field the server returns is
    retained, so this class does not by itself filter out sensitive fields.
    """

    model_config = ConfigDict(extra="allow")

    id: UUID
    name: str
    connector_type: str
    enabled: bool
    scopes: List[str] = []


class ConnectorCatalogRegister(BaseModel):
    """Register a connector *type* in the cluster's DB-backed catalog.

    Parity with registering an app template: the connector's self-describing
    manifest is stored so it surfaces in the admin catalog as a configurable
    entry, without editing the remote ``connectors.json``. Identity fields
    (``connector_type``/``provider_label``/``icon``) are derived from the manifest
    server-side. No config is stored — an admin fills it in afterwards.
    """

    model_config = ConfigDict(extra="allow")

    manifest: Dict[str, Any] = Field(
        ...,
        description="The connector's self-describing manifest (ConnectorSpec.to_manifest()).",
    )
    version: Optional[str] = Field(
        default=None, description="Optional catalog version tag (advisory)."
    )


class CatalogConnector(BaseModel):
    """A connector available to subscribe from the catalog (remote or DB-backed).

    ``manifest`` carries the connector's ``config_schema``/``config_fields`` so the
    admin UI can render its config form. ``already_subscribed`` flags types this
    deployment already has.
    """

    model_config = ConfigDict(extra="allow")

    connector_type: str
    provider_label: str
    icon: Optional[str] = None
    already_subscribed: bool = False
    manifest: Dict[str, Any]


# Deprecated aliases: these schemas were renamed ExternalConnector* -> Connector*
# (PR #193). The old names are kept so existing importers keep working; prefer the
# Connector* names in new code.
ExternalConnector = Connector
ExternalConnectorCreate = ConnectorCreate
ExternalConnectorUpdate = ConnectorUpdate
