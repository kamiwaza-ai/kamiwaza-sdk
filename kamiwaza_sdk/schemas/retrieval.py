"""Pydantic models for the retrieval service."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, SecretStr


class TransportType(str, Enum):
    INLINE = "inline"
    SSE = "sse"
    GRPC = "grpc"


class RetrievalRequest(BaseModel):
    dataset_urn: str
    transport: str = Field(default="auto")
    limit_rows: Optional[int] = Field(default=None, ge=1)
    offset: Optional[int] = Field(default=None, ge=0)
    filters: Optional[Dict[str, Any]] = None
    options: Optional[Dict[str, Any]] = None
    columns: Optional[List[str]] = None
    credential_override: Optional[SecretStr] = None
    format_hint: Optional[str] = None
    batch_size: Optional[int] = Field(default=None, ge=1)
    sdk_session: Optional[str] = None


class DatasetDescriptor(BaseModel):
    urn: str
    platform: str
    path: Optional[str] = None
    format: Optional[str] = None
    estimated_bytes: Optional[int] = None
    estimated_rows: Optional[int] = None


class InlineData(BaseModel):
    media_type: str
    data: Any
    row_count: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class JobProgress(BaseModel):
    bytes_processed: Optional[int] = None
    rows_processed: Optional[int] = None
    chunks_emitted: Optional[int] = None


class GrpcHandshake(BaseModel):
    endpoint: str
    token: str
    expires_at: datetime
    protocol: str = "kamiwaza.retrieval.v1"


class RetrievalJob(BaseModel):
    job_id: str
    transport: TransportType
    status: str
    dataset: DatasetDescriptor
    inline: Optional[InlineData] = None
    grpc: Optional[GrpcHandshake] = None


class RetrievalJobStatus(BaseModel):
    job_id: str
    status: str
    transport: TransportType
    dataset: DatasetDescriptor
    progress: JobProgress = Field(default_factory=JobProgress)
    created_at: datetime
    updated_at: datetime


class RetrievalStreamEvent(BaseModel):
    event: str
    data: Dict[str, Any]
