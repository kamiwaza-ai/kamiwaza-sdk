"""Pydantic models for the retrieval service."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    computed_field,
    model_validator,
)


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


class FlightEndpoint(BaseModel):
    model_config = ConfigDict(extra="allow")

    location: str


class GrpcHandshake(BaseModel):
    model_config = ConfigDict(extra="allow")

    endpoints: List[FlightEndpoint] = Field(default_factory=list)
    token: str = Field(repr=False)
    expires_at: datetime
    # Omitted discriminators belong to the legacy retrieval protocol. Flight
    # use must be explicitly advertised by the server as ``arrow-flight``.
    protocol: str = "kamiwaza.retrieval.v1"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def endpoint(self) -> Optional[str]:
        """Return the first endpoint location for legacy SDK callers."""
        if not self.endpoints:
            return None
        return self.endpoints[0].location

    @endpoint.setter
    def endpoint(self, value: Optional[str]) -> None:
        """Preserve mutation compatibility with the former singular field."""
        if value is None:
            self.endpoints = []
            return
        self.endpoints = [FlightEndpoint(location=value)]

    @model_validator(mode="before")
    @classmethod
    def _lift_legacy_endpoint(cls, data: Any) -> Any:
        """Tolerate legacy server payloads that send a single string ``endpoint``.

        Old servers return ``{"endpoint": "host:port", "token": ..., "expires_at": ...}``
        without the ``endpoints`` list.  When ``endpoints`` is absent or empty and a
        bare string ``endpoint`` key is present, lift it into the expected list shape.
        """
        if not isinstance(data, dict):
            return data
        endpoints = data.get("endpoints")
        if not endpoints:
            legacy = data.get("endpoint")
            if isinstance(legacy, str):
                data = dict(data)
                data["endpoints"] = [{"location": legacy}]
        if "endpoint" in data:
            data = dict(data)
            data.pop("endpoint")
        return data


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
