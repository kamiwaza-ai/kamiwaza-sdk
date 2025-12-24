"""Pydantic models for ingestion API interactions."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ActiveIngestRequest(BaseModel):
    source_type: str = Field(..., description="Ingestion plugin identifier (e.g., 's3')")
    kwargs: Dict[str, Any] = Field(default_factory=dict)


class MCPEmitRequest(BaseModel):
    mcp: Dict[str, Any]


class IngestJobCreate(BaseModel):
    job_id: str
    schedule: str
    source_type: str
    conn_args: Dict[str, Any] = Field(default_factory=dict)


class IngestJobStatus(BaseModel):
    job_id: str
    last_run: Optional[str] = None
    status: str = "pending"
    error_count: int = 0
    created_urns: List[str] = Field(default_factory=list)


class IngestResponse(BaseModel):
    urns: List[str] = Field(default_factory=list)
    status: str = "success"
    errors: List[str] = Field(default_factory=list)


class OperationStatus(BaseModel):
    status: str
