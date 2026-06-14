# kamiwaza_sdk/services/connectors.py

"""Client service for cluster-wide external connectors (M365, Google, …).

Registers the connector *app* (admin-scoped, one per type per cluster) — e.g.
the M365 tenant/client identifiers. The per-user OAuth connection is a separate
interactive Device Code Flow and is intentionally not wrapped here.
"""

from typing import List, Optional, Union
from uuid import UUID

from .base_service import BaseService
from ..exceptions import APIError, NotFoundError
from ..schemas.connectors import (
    M365_DEFAULT_SCOPES,
    ExternalConnector,
    ExternalConnectorCreate,
    M365ConnectorConfig,
)


class ConnectorService(BaseService):
    """Manage cluster-wide connector registrations."""

    def list(self) -> List[ExternalConnector]:
        """List registered connectors."""
        response = self.client.get("/connectors")
        items = (
            response.get("items", response) if isinstance(response, dict) else response
        )
        return [ExternalConnector.model_validate(item) for item in items]

    def get(self, connector_id: Union[str, UUID]) -> ExternalConnector:
        """Get a connector by id."""
        try:
            response = self.client.get(f"/connectors/{connector_id}")
            return ExternalConnector.model_validate(response)
        except APIError as e:
            if e.status_code == 404:
                raise NotFoundError(f"Connector '{connector_id}' not found") from e
            raise

    def create(self, request: ExternalConnectorCreate) -> ExternalConnector:
        """Register a connector (admin-scoped, cluster-wide)."""
        response = self.client.post(
            "/connectors", json=request.model_dump(mode="json")
        )
        return ExternalConnector.model_validate(response)

    def create_m365(
        self,
        *,
        tenant_id: str,
        client_id: str,
        name: str = "Microsoft 365",
        scopes: Optional[List[str]] = None,
        enabled: bool = True,
    ) -> ExternalConnector:
        """Register the cluster-wide M365 connector.

        ``tenant_id`` and ``client_id`` are public Azure AD identifiers (not
        secrets); no client secret is used (Device Code Flow). Each user later
        connects their own account interactively.

        Args:
            tenant_id: Azure AD tenant ID.
            client_id: App-registration client ID.
            name: Display name for the connector.
            scopes: Graph scopes to request; defaults to the standard M365 set.
            enabled: Whether the connector is enabled.

        Returns:
            ExternalConnector: The registered connector.
        """
        request = ExternalConnectorCreate(
            name=name,
            connector_type="m365",
            config=M365ConnectorConfig(
                tenant_id=tenant_id, client_id=client_id
            ).model_dump(),
            scopes=scopes or list(M365_DEFAULT_SCOPES),
            enabled=enabled,
        )
        return self.create(request)

    def delete(self, connector_id: Union[str, UUID]) -> bool:
        """Delete a connector by id."""
        try:
            self.client.delete(f"/connectors/{connector_id}")
            return True
        except APIError as e:
            if e.status_code == 404:
                raise NotFoundError(f"Connector '{connector_id}' not found") from e
            raise
