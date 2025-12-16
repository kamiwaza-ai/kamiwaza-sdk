"""Pydantic models for catalog datasets, containers, and secrets."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, SecretStr


class SchemaField(BaseModel):
    name: str
    type: str
    description: Optional[str] = None


class Schema(BaseModel):
    name: str
    platform: str
    version: Optional[int] = None
    fields: List[SchemaField]


class DatasetCreate(BaseModel):
    name: str
    platform: str
    environment: str = "PROD"
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    properties: Dict[str, Any] = Field(default_factory=dict)
    dataset_schema: Optional[Schema] = None
    container_urn: Optional[str] = None


class Dataset(DatasetCreate):
    urn: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DatasetUpdate(BaseModel):
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    properties: Optional[Dict[str, Any]] = None
    dataset_schema: Optional[Schema] = None
    container_urn: Optional[str] = None


class ContainerCreate(BaseModel):
    name: str
    platform: Optional[str] = None
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    properties: Dict[str, Any] = Field(default_factory=dict)
    parent_urn: Optional[str] = None


class Container(ContainerCreate):
    urn: str
    sub_containers: List[str] = Field(default_factory=list)
    datasets: List[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ContainerUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    properties: Optional[Dict[str, Any]] = None
    parent_urn: Optional[str] = None


class SecretCreate(BaseModel):
    name: str
    value: SecretStr
    owner: str
    description: Optional[str] = None


class Secret(BaseModel):
    urn: str
    name: str
    owner: Optional[str] = None
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SecretUpdate(BaseModel):
    value: Optional[SecretStr] = None
    owner: Optional[str] = None
    description: Optional[str] = None
