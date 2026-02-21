# kamiwaza_sdk/schemas/workrooms.py

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class WorkroomType(str, Enum):
    """Workroom lifecycle type."""

    ephemeral = "ephemeral"
    persistent = "persistent"


class WorkroomStatus(str, Enum):
    """Workroom lifecycle status."""

    active = "active"
    archived = "archived"
    purging = "purging"
    deleted = "deleted"


class CreateWorkroom(BaseModel):
    """Request to create a new workroom."""

    name: str = Field(..., min_length=1, max_length=255)
    type: WorkroomType = Field(..., description="ephemeral or persistent")
    description: Optional[str] = Field(default=None, max_length=1024)
    labels: Optional[List[str]] = None
    classification: Optional[str] = Field(default=None, max_length=255)
    attributes: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Extensible key-value pairs (mission_id, template_id, etc.)",
    )
    scg_references: Optional[List[str]] = Field(
        default=None,
        description="Security Classification Guide identifiers",
    )


class UpdateWorkroom(BaseModel):
    """Request for partial update of workroom metadata."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=1024)
    labels: Optional[List[str]] = None
    classification: Optional[str] = Field(default=None, max_length=255)
    attributes: Optional[Dict[str, Any]] = None
    scg_references: Optional[List[str]] = None


class Workroom(BaseModel):
    """Full workroom entity returned by the API."""

    id: UUID
    tenant_id: str
    owner_user_id: str
    name: str
    type: str
    description: Optional[str] = None
    labels: Optional[List[str]] = None
    classification: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = None
    scg_references: Optional[List[str]] = None
    status: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None


class DeleteWorkroomResponse(BaseModel):
    """Response from delete/purge operation."""

    workroom_id: UUID
    status: str
    message: str


class ExportManifestItem(BaseModel):
    """A single item in the export manifest."""

    type: str = Field(..., description="Resource type: metadata, dataset, etc.")
    name: str = Field(..., description="Human-readable resource name")
    exportable: bool = Field(..., description="Whether this item can be exported")
    reason: Optional[str] = Field(
        default=None,
        description="Explanation if not exportable",
    )


class ExportManifest(BaseModel):
    """Categorized list of workroom contents with export eligibility."""

    workroom_id: UUID
    items: List[ExportManifestItem]


class IngestionSummary(BaseModel):
    """Aggregated ingestion statistics for a workroom."""

    workroom_id: UUID
    total_sources: int = 0
    counts_by_source_type: Dict[str, int] = Field(default_factory=dict)
    date_range_start: Optional[datetime] = None
    date_range_end: Optional[datetime] = None
    error_count: int = 0
    warning_count: int = 0
    catalog_entries: int = 0
