# kamiwaza_sdk/schemas/apps.py

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
from uuid import UUID
from enum import Enum


class TemplateSource(str, Enum):
    kamiwaza = "kamiwaza"
    user_repo = "user_repo"
    public = "public"


class TemplateVisibility(str, Enum):
    private = "private"
    team = "team"
    public = "public"


class RiskTier(int, Enum):
    guided = 0
    scanned = 1
    break_glass = 2


class AppTemplate(BaseModel):
    """Application template information."""
    id: UUID
    name: str
    version: Optional[str] = None
    source_type: TemplateSource
    visibility: TemplateVisibility
    compose_yml: str
    risk_tier: RiskTier
    verified: bool = False
    created_at: datetime
    updated_at: Optional[datetime] = None
    description: Optional[str] = None
    env_defaults: Optional[Dict[str, str]] = None


class CreateAppDeployment(BaseModel):
    """Request to deploy an application."""
    name: str = Field(..., description="Name of the app deployment")
    template_id: Optional[UUID] = Field(None, description="The UUID of the app template to use")
    min_copies: int = Field(default=1, description="Minimum number of copies to maintain")
    starting_copies: int = Field(default=1, description="Number of copies to start with")
    max_copies: Optional[int] = Field(default=None, description="Maximum number of copies allowed")
    env_vars: Optional[Dict[str, str]] = Field(None, description="Environment variables to pass to the app container")


class AppDeployment(BaseModel):
    """Application deployment information."""
    id: UUID
    name: str
    template_id: Optional[UUID] = None
    requested_at: datetime
    deployed_at: Optional[datetime] = None
    status: str = "UNINITIALIZED"
    created_at: datetime
    min_copies: int = 1
    starting_copies: int = 1
    max_copies: Optional[int] = None
    env_vars: Optional[Dict[str, str]] = None
    compose_yml: Optional[str] = None


class AppInstance(BaseModel):
    """Application instance information."""
    id: UUID
    deployment_id: UUID
    deployed_at: datetime
    container_id: Optional[str] = None
    node_id: Optional[UUID] = None
    host_name: Optional[str] = None
    listen_port: Optional[int] = None
    status: Optional[str] = "UNINITIALIZED"
    port_mappings: List[Dict[str, Any]] = Field(default_factory=list)


class ImageStatus(BaseModel):
    """Docker image pull status."""
    template_id: UUID
    images: List[str]
    image_status: Dict[str, bool]
    all_images_pulled: bool


class ImagePullResult(BaseModel):
    """Result of pulling Docker images."""
    template_id: UUID
    total_images: int
    successful_pulls: int
    results: List[Dict[str, Any]]
    all_successful: bool


class GardenApp(BaseModel):
    """Pre-built garden application."""
    name: str
    version: str
    description: str
    compose_yml: str
    env_defaults: Optional[Dict[str, str]] = None
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    author: Optional[str] = None
    license: Optional[str] = None
    homepage: Optional[str] = None