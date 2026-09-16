"""Client helpers for enclave connectors and documents."""

from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import UUID

from .base_service import BaseService
from ..schemas.enclaves import (
    ConnectorCreate,
    ConnectorListResponse,
    ConnectorResponse,
    ConnectorUpdate,
    DocumentListResponse,
    DocumentRecord,
    IndexDocumentRequest,
    TriggerResponse,
)


class ConnectorClient(BaseService):
    """CRUD helpers for enclave connectors."""

    _BASE_PATH = "/enclaves/connectors"

    def list(
        self,
        *,
        limit: int | None = 50,
        offset: int | None = 0,
        source_type: str | None = None,
        enabled: bool | None = None,
        tag: str | None = None,
    ) -> ConnectorListResponse:
        """List enclave connectors, filtered and paged.

        Args:
            limit: Maximum connectors to return.
            offset: Number of connectors to skip.
            source_type: Keep only connectors of this source type.
            enabled: Keep only enabled or only disabled connectors.
            tag: Keep only connectors carrying this tag.

        Returns:
            ConnectorListResponse: The matching page of connectors.
        """
        params: Dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        if source_type:
            params["source_type"] = source_type
        if enabled is not None:
            params["enabled"] = enabled
        if tag:
            params["tag"] = tag
        response = self.client.get(f"{self._BASE_PATH}/", params=params or None)
        return ConnectorListResponse.model_validate(response)

    def create(self, payload: ConnectorCreate) -> ConnectorResponse:
        """Register an enclave connector against an external source.

        Registering does not ingest anything. Call ``trigger_ingest`` to start
        the first run.

        Args:
            payload: Source type, credentials reference and connector options.

        Returns:
            ConnectorResponse: The registered connector.
        """
        response = self.client.post(
            f"{self._BASE_PATH}/",
            json=payload.model_dump(mode="json", exclude_unset=True),
        )
        return ConnectorResponse.model_validate(response)

    def get(self, connector_id: UUID | str) -> ConnectorResponse:
        """Fetch one enclave connector by identifier.

        Args:
            connector_id: Identifier of the connector.

        Returns:
            ConnectorResponse: The connector's current configuration.
        """
        connector = _ensure_uuid(connector_id, field="connector_id")
        response = self.client.get(f"{self._BASE_PATH}/{connector}")
        return ConnectorResponse.model_validate(response)

    def update(
        self, connector_id: UUID | str, payload: ConnectorUpdate
    ) -> ConnectorResponse:
        """Update an enclave connector's configuration in place.

        Args:
            connector_id: Identifier of the connector to update.
            payload: Fields to change.

        Returns:
            ConnectorResponse: The connector as it now stands.
        """
        connector = _ensure_uuid(connector_id, field="connector_id")
        response = self.client.put(
            f"{self._BASE_PATH}/{connector}",
            json=payload.model_dump(mode="json", exclude_unset=True),
        )
        return ConnectorResponse.model_validate(response)

    def delete(self, connector_id: UUID | str) -> None:
        """Delete an enclave connector, stopping any further ingestion from it.

        Args:
            connector_id: Identifier of the connector to delete.
        """
        connector = _ensure_uuid(connector_id, field="connector_id")
        self.client.delete(
            f"{self._BASE_PATH}/{connector}",
            expect_json=False,
        )
        return None

    def trigger_ingest(self, connector_id: UUID | str) -> TriggerResponse:
        """Start an ingestion run for one enclave connector.

        Returns as soon as the run is accepted; the run itself continues on the
        platform, so poll the connector or list its documents to follow it.

        Args:
            connector_id: Identifier of the connector to run.

        Returns:
            TriggerResponse: The accepted run's status.
        """
        connector = _ensure_uuid(connector_id, field="connector_id")
        response = self.client.post(
            f"{self._BASE_PATH}/{connector}/trigger_ingest"
        )
        return TriggerResponse.model_validate(response)


class DocumentClient(BaseService):
    """Helpers for enclave document indexing and retrieval."""

    _BASE_PATH = "/enclaves/documents"

    def create(self, payload: IndexDocumentRequest) -> DocumentRecord:
        """Index one document into an enclave source.

        Args:
            payload: The document's content, source and metadata.

        Returns:
            DocumentRecord: The indexed document's record.
        """
        response = self.client.post(
            f"{self._BASE_PATH}/",
            json=payload.model_dump(mode="json", exclude_unset=True),
        )
        return DocumentRecord.model_validate(response)

    def list(
        self,
        source_id: UUID | str,
        *,
        limit: int | None = 20,
        offset: int | None = 0,
        item_type: str | None = None,
        tag: str | None = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> DocumentListResponse:
        """List indexed documents for one enclave source, filtered and paged.

        Args:
            source_id: Identifier of the enclave source.
            limit: Maximum documents to return.
            offset: Number of documents to skip.
            item_type: Keep only documents of this item type.
            tag: Keep only documents carrying this tag.
            headers: Extra request headers.

        Returns:
            DocumentListResponse: The matching page of documents.
        """
        source = _ensure_uuid(source_id, field="source_id")
        params: Dict[str, Any] = {"source_id": str(source)}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        if item_type:
            params["item_type"] = item_type
        if tag:
            params["tag"] = tag

        response = self.client.get(
            f"{self._BASE_PATH}/",
            params=params,
            headers=headers or None,
        )
        return DocumentListResponse.model_validate(response)

    def get(
        self,
        document_id: UUID | str,
        *,
        source_id: UUID | str,
        headers: Optional[Dict[str, str]] = None,
    ) -> DocumentRecord:
        """Fetch one indexed enclave document by identifier.

        Args:
            document_id: Identifier of the document.
            source_id: Identifier of the enclave source holding it.
            headers: Extra request headers.

        Returns:
            DocumentRecord: The document's record.
        """
        params = {"source_id": str(_ensure_uuid(source_id, field="source_id"))}
        response = self.client.get(
            f"{self._BASE_PATH}/{_ensure_uuid(document_id, field='document_id')}",
            params=params,
            headers=headers or None,
        )
        return DocumentRecord.model_validate(response)


class EnclavesService(BaseService):
    """High-level facade for enclave operations."""

    def __init__(self, client):
        super().__init__(client)
        self.connectors = ConnectorClient(client)
        self.documents = DocumentClient(client)


def _ensure_uuid(value: UUID | str, *, field: str) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {field}: expected UUID, got {value!r}") from exc
