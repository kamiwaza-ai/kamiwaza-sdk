"""Typed connector-spec models for ``catalog.register_from_spec`` (ENG-6964).

These mirror the engine's published connector contract (CSE-SPEC-1) so SDK
callers get IDE help + light validation. The engine is the authoritative
validator (``extra='forbid'`` drift guard); these models use ``extra='allow'``
for forward compatibility per SDK convention and let the engine reject any
genuinely-unknown field.

A connector spec NEVER carries secret values — credentials are referenced by a
catalog secret URN via ``auth.credential_ref`` (INF-CB5).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EndpointSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    method: Literal["GET", "POST"]
    path: str
    body_template: dict[str, Any] = Field(default_factory=dict)
    items_path: str


class PaginationSpec(BaseModel):
    """Bounded pagination; no full-corpus scroll."""

    model_config = ConfigDict(extra="allow")

    max_pages: int = Field(ge=1)
    page_size: int = Field(default=100, ge=1)
    max_records: int | None = Field(default=None, ge=1)


class AuthSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: Literal["none", "api_key", "basic", "bearer", "sigv4"]
    # A secret URN (e.g. ``urn:li:secret:...``), never a secret value (INF-CB5).
    credential_ref: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class GateSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str = Field(min_length=1)  # a gate classpath string, not code
    config: dict[str, Any] = Field(default_factory=dict)


class ConnectorSpec(BaseModel):
    """The published connector contract (mirrors the engine's CSE-SPEC-1)."""

    model_config = ConfigDict(extra="allow")

    spec_version: Literal["connector-spec.v1"] = "connector-spec.v1"
    platform: str  # lineage metadata, not a dispatch key (DP-1)
    base_url: str = Field(pattern=r"^https?://.+")
    endpoint: EndpointSpec
    index: str
    pagination: PaginationSpec
    auth: AuthSpec
    data_attribute_fields: list[str]  # projected onto every record (INF-CB4)
    gate: GateSpec
    field_mappings: dict[str, str] | None = None  # renaming only
    time_field: str | None = None
    filterable_fields: list[str] = Field(default_factory=list)
