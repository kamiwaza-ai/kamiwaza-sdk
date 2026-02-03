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
        response = self.client.post(
            f"{self._BASE_PATH}/",
            json=payload.model_dump(mode="json", exclude_none=True),
        )
        return ConnectorResponse.model_validate(response)

    def get(self, connector_id: UUID | str) -> ConnectorResponse:
        response = self.client.get(f"{self._BASE_PATH}/{connector_id}")
        return ConnectorResponse.model_validate(response)

    def update(self, connector_id: UUID | str, payload: ConnectorUpdate) -> ConnectorResponse:
        response = self.client.put(
            f"{self._BASE_PATH}/{connector_id}",
            json=payload.model_dump(mode="json", exclude_none=True),
        )
        return ConnectorResponse.model_validate(response)

    def delete(self, connector_id: UUID | str) -> ConnectorResponse:
        response = self.client.delete(f"{self._BASE_PATH}/{connector_id}")
        return ConnectorResponse.model_validate(response)

    def trigger_ingest(self, connector_id: UUID | str) -> TriggerResponse:
        response = self.client.post(f"{self._BASE_PATH}/{connector_id}/trigger_ingest")
        return TriggerResponse.model_validate(response)


class DocumentClient(BaseService):
    """Helpers for enclave document indexing and retrieval."""

    _BASE_PATH = "/enclaves/documents"
    _SYSTEM_HIGH_HEADER = "X-User-System-High"

    def create(self, payload: IndexDocumentRequest) -> DocumentRecord:
        response = self.client.post(
            f"{self._BASE_PATH}/",
            json=payload.model_dump(mode="json", exclude_none=True),
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
        system_high: str | None = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> DocumentListResponse:
        params: Dict[str, Any] = {"source_id": str(source_id)}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        if item_type:
            params["item_type"] = item_type
        if tag:
            params["tag"] = tag

        request_headers = dict(headers or {})
        if system_high:
            request_headers.setdefault(self._SYSTEM_HIGH_HEADER, system_high)

        response = self.client.get(
            f"{self._BASE_PATH}/",
            params=params,
            headers=request_headers or None,
        )
        return DocumentListResponse.model_validate(response)

    def get(
        self,
        document_id: UUID | str,
        *,
        source_id: UUID | str,
        system_high: str | None = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> DocumentRecord:
        params = {"source_id": str(source_id)}
        request_headers = dict(headers or {})
        if system_high:
            request_headers.setdefault(self._SYSTEM_HIGH_HEADER, system_high)
        response = self.client.get(
            f"{self._BASE_PATH}/{document_id}",
            params=params,
            headers=request_headers or None,
        )
        return DocumentRecord.model_validate(response)


class EnclavesService(BaseService):
    """High-level facade for enclave operations."""

    def __init__(self, client):
        super().__init__(client)
        self.connectors = ConnectorClient(client)
        self.documents = DocumentClient(client)
