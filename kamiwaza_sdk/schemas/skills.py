"""Pydantic models for the Skills Library API."""

from __future__ import annotations

from datetime import datetime
from typing import IO, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PackageSummary(BaseModel):
    """Summary of an imported or stored skill package."""

    model_config = ConfigDict(extra="allow")

    root_dir: str
    entries: list[str] = Field(default_factory=list)
    package_size_bytes: int
    has_scripts: bool = False
    has_references: bool = False
    has_assets: bool = False


class SkillLibraryListItem(BaseModel):
    """Summary item returned by the skills list endpoint."""

    model_config = ConfigDict(extra="allow")

    id: UUID
    name: str
    display_name: str
    description: str
    category: str
    status: str
    classification: str | None = None
    tags: list[str] = Field(default_factory=list)
    content_checksum: str
    created_at: datetime
    updated_at: datetime | None = None


class SkillLibraryListResponse(BaseModel):
    """Paginated response from the skills list endpoint."""

    model_config = ConfigDict(extra="allow")

    items: list[SkillLibraryListItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class SkillLibraryDetailResponse(BaseModel):
    """Full detail response for a single skill."""

    model_config = ConfigDict(extra="allow")

    id: UUID
    name: str
    display_name: str
    description: str
    category: str
    trigger: Any | None = None
    inputs: list[Any] | None = None
    classification: str | None = None
    status: str
    tags: list[str] = Field(default_factory=list)
    content_checksum: str
    metadata: dict[str, Any] | None = None
    package_summary: PackageSummary
    created_by: str
    created_at: datetime
    updated_at: datetime | None = None


class SkillLibraryUpdateRequest(BaseModel):
    """Metadata update request for a skill."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    classification: str | None = Field(default=None, max_length=255)
    status: str | None = Field(default=None, min_length=1, max_length=20)
    trigger: Any | None = None
    inputs: list[Any] | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_non_empty(self) -> "SkillLibraryUpdateRequest":
        """Require at least one field when updating metadata."""
        if not self.model_dump(exclude_unset=True):
            raise ValueError("At least one field must be provided")
        return self


class SkillLibraryExportRequest(BaseModel):
    """Request body for exporting one or more skills."""

    model_config = ConfigDict(extra="forbid")

    skill_ids: list[UUID] = Field(min_length=1, max_length=100)


class SkillPackageDownload(BaseModel):
    """Typed representation of a zip download response."""

    model_config = ConfigDict(extra="allow")

    filename: str
    content_type: str
    content: bytes = Field(repr=False)


SkillPackageContent = bytes | IO[bytes]
