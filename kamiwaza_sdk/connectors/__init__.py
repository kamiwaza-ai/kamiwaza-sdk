"""Kamiwaza connector server SPI.

The contract a connector pod implements: subclass
:class:`ConnectorProvider`, declare a :class:`ConfigSchema`, and serve it with
:func:`create_connector_app`. ``provider.to_manifest()`` is the connector's full
self-description (identity / auth / egress / deployment + the admin-form config
JSON Schema) — the same document published to the catalog (``connectors.json``)
and served at ``GET /manifest``.

This is the server-side complement to the connector *client* surface
(:mod:`kamiwaza_sdk.services.connectors`, :mod:`kamiwaza_sdk.schemas.connectors`).
Connector repos depend on ``kamiwaza-sdk[connector]`` and import from here
instead of vendoring the platform source.
"""

from __future__ import annotations

from .config_schema import ConfigField, ConfigSchema, ConfigType
from .connector_proxy import ConnectorProxyRequest, ConnectorProxyResponse
from .connector_token import ConnectorMintRequest, ConnectorMintResponse
from .exceptions import ConnectorException, InvalidConfigException
from .provider import (
    AuthModel,
    ConnectorProvider,
    ConnectorSpec,
    ConstraintDescriptor,
    DeploymentDescriptor,
    OAuthDescriptor,
    PerUserOAuth,
    ServiceToken,
    SurfaceDescriptor,
    auth_model_from_kind,
    validate_icon,
)
from .server_kit import (
    ExecuteRequest,
    OpResult,
    VerifyRequest,
    create_connector_app,
)

__all__ = [
    "ConfigField",
    "ConfigSchema",
    "ConfigType",
    "AuthModel",
    "PerUserOAuth",
    "ServiceToken",
    "auth_model_from_kind",
    "OAuthDescriptor",
    "DeploymentDescriptor",
    "ConstraintDescriptor",
    "SurfaceDescriptor",
    "ConnectorSpec",
    "ConnectorProvider",
    "validate_icon",
    "create_connector_app",
    "ExecuteRequest",
    "VerifyRequest",
    "OpResult",
    "ConnectorProxyRequest",
    "ConnectorProxyResponse",
    "ConnectorMintRequest",
    "ConnectorMintResponse",
    "ConnectorException",
    "InvalidConfigException",
]
