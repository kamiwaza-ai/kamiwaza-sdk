"""Pydantic models for enclave connectors and documents."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .markings import Marking


class EnclaveBaseModel(BaseModel):
    """Base model enabling forward-compatible parsing."""

    model_config = ConfigDict(extra="allow")


class ConnectorBase(EnclaveBaseModel):
    name: str
    source_type: str
    connector_type: str
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    allowed_roles: List[str] = Field(default_factory=list)
    require_encryption: bool = True
    level_boundary: Optional[str] = None
    default_marking: Optional[str] = Field(
        default=None,
        description="Default marking applied when documents lack explicit markings",
    )


class ConnectorCreate(ConnectorBase):
    connection_config: Dict[str, Any]


class ConnectorUpdate(EnclaveBaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    allowed_roles: Optional[List[str]] = None
    require_encryption: Optional[bool] = None
    enabled: Optional[bool] = None
    connection_config: Optional[Dict[str, Any]] = None
    level_boundary: Optional[str] = None
    default_marking: Optional[str] = None


class ConnectorResponse(EnclaveBaseModel):
    id: UUID
    name: str
    source_type: str
    connector_type: str
    description: Optional[str] = None
    tags: List[str]
    allowed_roles: List[str]
    require_encryption: bool
    enabled: bool
    level_boundary: Optional[str] = None
    default_marking: Optional[str] = None
    last_ingestion_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    error_count: int
    created_at: datetime
    created_by: str
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None


class ConnectorListResponse(EnclaveBaseModel):
    items: List[ConnectorResponse]
    total: int
    limit: int
    offset: int


class TriggerResponse(EnclaveBaseModel):
    status: str


class IndexDocumentRequest(EnclaveBaseModel):
    source_id: UUID
    source_ref: str
    item_type: str = Field(default="document")
    job_id: Optional[UUID] = None
    job_name: Optional[str] = None
    job_config: Optional[Dict[str, Any]] = None
    marking_text: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RejectedDocument(EnclaveBaseModel):
    document_id: str
    reason: Optional[str] = None


class DocumentRecord(EnclaveBaseModel):
    id: UUID
    source_id: UUID
    job_id: Optional[UUID] = None
    source_ref: str
    item_type: str
    title: Optional[str] = None
    description: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    tags: List[str] = Field(default_factory=list)
    categories: List[str] = Field(default_factory=list)
    language: Optional[str] = None
    marking: Optional[Marking] = None
    level_id: Optional[str] = None
    entities: Optional[Dict[str, Any]] = None
    indexed_at: datetime
    content_date: Optional[datetime] = None
    confidence_score: Optional[float] = None
    completeness_score: Optional[float] = None
    access_count: int = 0


class DocumentListResponse(EnclaveBaseModel):
    items: List[DocumentRecord]
    total: int
    limit: int
    offset: int
    rejections: List[RejectedDocument] = Field(default_factory=list)
