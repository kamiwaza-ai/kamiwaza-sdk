# kamiwaza_sdk/services/connectors.py

"""Client service for cluster-wide connectors (M365, Google, …).

Registers the connector *app* (admin-scoped, one per type per cluster) — e.g.
the M365 tenant/client identifiers, or an out-of-core connector by manifest +
endpoint. The per-user OAuth connection is a separate interactive Device Code
Flow and is intentionally not wrapped here. The client is type-agnostic: a
``connector_type`` is an open string the platform resolves against the catalog,
so new connectors need no SDK change (per-type seeding lives in the seeder).
"""

from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from .base_service import BaseService
from ..exceptions import APIError, NotFoundError
from ..schemas.connectors import (
    AvailableConnector,
    Connector,
    ConnectorCreate,
    ConnectorSubscriptionCreate,
    ConnectorUpdate,
)


class ConnectorService(BaseService):
    """Manage cluster-wide connector registrations."""

    def list(self) -> List[Connector]:
        """List registered connectors."""
        response = self.client.get("/connectors")
        items = (
            response.get("items", response) if isinstance(response, dict) else response
        )
        return [Connector.model_validate(item) for item in items]

    def list_available(self) -> List[AvailableConnector]:
        """List enabled connectors as user-safe metadata.

        Mirrors the platform's ``/connectors/available`` endpoint used to drive
        per-user account-connection UIs: only enabled connectors, with
        non-sensitive fields. Use :meth:`list` for the full admin view.
        """
        response = self.client.get("/connectors/available")
        items = (
            response.get("items", response) if isinstance(response, dict) else response
        )
        return [AvailableConnector.model_validate(item) for item in items]

    def get(self, connector_id: Union[str, UUID]) -> Connector:
        """Get a connector by id."""
        try:
            response = self.client.get(f"/connectors/{connector_id}")
            return Connector.model_validate(response)
        except APIError as e:
            if e.status_code == 404:
                raise NotFoundError(f"Connector '{connector_id}' not found") from e
            raise

    def update(
        self, connector_id: Union[str, UUID], request: ConnectorUpdate
    ) -> Connector:
        """Update a connector's mutable fields (name/config/scopes/enabled).

        Only the fields set on ``request`` are sent; unset (``None``) fields are
        left unchanged. Raises :class:`NotFoundError` if the connector is absent.
        """
        try:
            response = self.client.put(
                f"/connectors/{connector_id}",
                json=request.model_dump(mode="json", exclude_none=True),
            )
            return Connector.model_validate(response)
        except APIError as e:
            if e.status_code == 404:
                raise NotFoundError(f"Connector '{connector_id}' not found") from e
            raise

    def create(self, request: ConnectorCreate) -> Connector:
        """Register a connector (admin-scoped, cluster-wide).

        ``request.connector_type`` is resolved against the published catalog;
        the platform attaches the matching manifest. For an out-of-core connector
        not in the catalog, use :meth:`subscribe` with its manifest + endpoint.
        """
        response = self.client.post(
            "/connectors", json=request.model_dump(mode="json")
        )
        return Connector.model_validate(response)

    def subscribe(
        self,
        *,
        manifest: Dict[str, Any],
        endpoint: str,
        config: Optional[Dict[str, Any]] = None,
        scopes: Optional[List[str]] = None,
        workload_principal_id: Optional[str] = None,
    ) -> Connector:
        """Subscribe an out-of-core connector by manifest + endpoint (admin-scoped).

        The connector runs out-of-process behind an HTTP/MCP ``endpoint``; the
        platform stores its self-describing ``manifest`` (a
        ``ConnectorSpec.to_manifest()`` dict) and registers a remote provider so
        it participates in the registry/catalog like a built-in connector. A
        ``connector_type`` already served by a built-in or an existing
        subscription is rejected by the platform (HTTP 400).

        Args:
            manifest: The connector's self-describing manifest.
            endpoint: HTTP/MCP endpoint where the connector is reached.
            config: Optional secret config (e.g. a service token); the platform
                stores it encrypted. Omit for connectors that need no credential.
            workload_principal_id: Service-account principal permitted to mint
                per-user tokens for this connector out-of-core; bound here at
                install. Omit for connectors that never mint out-of-core.

        Returns:
            Connector: The registered subscription.
        """
        request = ConnectorSubscriptionCreate(
            manifest=manifest,
            endpoint=endpoint,
            config=config or {},
            scopes=scopes or [],
            workload_principal_id=workload_principal_id,
        )
        response = self.client.post(
            "/connectors/subscriptions", json=request.model_dump(mode="json")
        )
        return Connector.model_validate(response)

    def delete(self, connector_id: Union[str, UUID]) -> bool:
        """Delete a connector by id."""
        try:
            self.client.delete(f"/connectors/{connector_id}")
            return True
        except APIError as e:
            if e.status_code == 404:
                raise NotFoundError(f"Connector '{connector_id}' not found") from e
            raise
