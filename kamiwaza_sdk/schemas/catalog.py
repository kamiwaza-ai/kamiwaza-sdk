# kamiwaza_sdk/schemas/catalog.py

from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
from datetime import datetime


# ===== Schema-related models =====

class SchemaField(BaseModel):
    """Individual field in a dataset schema."""
    name: str = Field(..., description="Field name")
    type: str = Field(..., description="Field data type")
    description: Optional[str] = Field(None, description="Field description")

    model_config = {"extra": "allow"}


class Schema(BaseModel):
    """Dataset schema definition."""
    name: str = Field(..., description="Schema name")
    platform: str = Field(..., description="Platform identifier")
    version: Optional[int] = Field(None, description="Schema version")
    fields: List[SchemaField] = Field(..., description="Schema fields")

    model_config = {"extra": "allow"}


# ===== Dataset models =====

class DatasetCreate(BaseModel):
    """Schema for creating a new dataset."""
    name: str = Field(..., description="Dataset name")
    platform: str = Field(..., description="Platform identifier")
    environment: str = Field(default="PROD", description="Environment (PROD, DEV, etc.)")
    description: Optional[str] = Field(None, description="Dataset description")
    tags: List[str] = Field(default_factory=list, description="Dataset tags")
    properties: Dict[str, Any] = Field(default_factory=dict, description="Custom properties")
    dataset_schema: Optional[Schema] = Field(None, description="Dataset schema")
    container_urn: Optional[str] = Field(None, description="Parent container URN")

    model_config = {"extra": "allow"}


class Dataset(BaseModel):
    """Full dataset schema including system-generated fields."""
    urn: str = Field(..., description="Dataset URN")
    name: str = Field(..., description="Dataset name")
    platform: str = Field(..., description="Platform identifier")
    environment: str = Field(..., description="Environment")
    description: Optional[str] = Field(None, description="Dataset description")
    tags: List[str] = Field(default_factory=list, description="Dataset tags")
    properties: Dict[str, Any] = Field(default_factory=dict, description="Custom properties")
    created_at: Optional[datetime] = Field(None, description="Creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")

    model_config = {"extra": "allow"}


class DatasetUpdate(BaseModel):
    """Schema for updating a dataset."""
    description: Optional[str] = Field(None, description="Dataset description")
    tags: Optional[List[str]] = Field(None, description="Dataset tags")
    properties: Optional[Dict[str, Any]] = Field(None, description="Custom properties")
    container_urn: Optional[str] = Field(None, description="Parent container URN")

    model_config = {"extra": "allow"}


# ===== Container models =====

class ContainerCreate(BaseModel):
    """Schema for creating a new container."""
    name: str = Field(..., description="Container name")
    platform: Optional[str] = Field(None, description="Platform identifier")
    description: Optional[str] = Field(None, description="Container description")
    tags: List[str] = Field(default_factory=list, description="Container tags")
    properties: Dict[str, Any] = Field(default_factory=dict, description="Custom properties")
    parent_urn: Optional[str] = Field(None, description="Parent container URN")

    model_config = {"extra": "allow"}


class Container(BaseModel):
    """Full container schema including system-generated fields."""
    urn: str = Field(..., description="Container URN")
    name: str = Field(..., description="Container name")
    platform: Optional[str] = Field(None, description="Platform identifier")
    description: Optional[str] = Field(None, description="Container description")
    tags: List[str] = Field(default_factory=list, description="Container tags")
    properties: Dict[str, Any] = Field(default_factory=dict, description="Custom properties")
    sub_containers: List[str] = Field(default_factory=list, description="Sub-container URNs")
    datasets: List[str] = Field(default_factory=list, description="Dataset URNs in this container")
    created_at: Optional[datetime] = Field(None, description="Creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")

    model_config = {"extra": "allow"}


class ContainerUpdate(BaseModel):
    """Schema for updating a container."""
    name: Optional[str] = Field(None, description="Container name")
    description: Optional[str] = Field(None, description="Container description")
    tags: Optional[List[str]] = Field(None, description="Container tags")
    properties: Optional[Dict[str, Any]] = Field(None, description="Custom properties")
    parent_urn: Optional[str] = Field(None, description="Parent container URN")

    model_config = {"extra": "allow"}

class Lineage(BaseModel):
    model_config = {
        "extra": "allow"
    }

class Tags(BaseModel):
    model_config = {
        "extra": "allow"
    }

class Terms(BaseModel):
    model_config = {
        "extra": "allow"
    }

class Ownership(BaseModel):
    model_config = {
        "extra": "allow"
    }

class Domains(BaseModel):
    model_config = {
        "extra": "allow"
    }

class Deprecation(BaseModel):
    model_config = {
        "extra": "allow"
    }

class Description(BaseModel):
    model_config = {
        "extra": "allow"
    }

class CustomProperties(BaseModel):
    model_config = {
        "extra": "allow"
    }

class MLSystems(BaseModel):
    model_config = {
        "extra": "allow"
    }