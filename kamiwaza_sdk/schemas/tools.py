# kamiwaza_sdk/schemas/tools.py

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
from uuid import UUID
from enum import Enum


class CreateToolDeployment(BaseModel):
    """Request to deploy a Tool server."""
    name: str = Field(..., description="Name for the Tool deployment")
    image: str = Field(..., description="Docker image for the Tool server")
    env_vars: Optional[Dict[str, str]] = Field(default_factory=dict, description="Environment variables")
    min_copies: int = Field(default=1, description="Minimum number of instances")
    max_copies: int = Field(default=1, description="Maximum number of instances")


class ToolDeployment(BaseModel):
    """Tool deployment information with generated URL."""
    id: UUID
    name: str
    template_id: Optional[UUID] = None
    requested_at: datetime
    deployed_at: Optional[datetime] = None
    status: str = "UNINITIALIZED"
    created_at: datetime
    compose_yml: Optional[str] = None
    min_copies: int = 1
    max_copies: Optional[int] = None
    env_vars: Optional[Dict[str, str]] = None
    
    # Tool-specific fields
    url: str = Field(..., description="Public URL for the Tool server (MCP endpoint)")
    deployment_type: str = Field(default="tool", description="Type of deployment")


class ToolCapability(BaseModel):
    """Represents a capability or tool provided by a Tool server."""
    name: str
    description: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None


class ToolServerInfo(BaseModel):
    """Information about a deployed Tool server."""
    deployment_id: UUID
    name: str
    url: str
    status: str
    capabilities: Optional[List[ToolCapability]] = None
    created_at: datetime


class ToolDiscovery(BaseModel):
    """Response for Tool server discovery."""
    servers: List[ToolServerInfo]
    total: int


class ToolHealthCheck(BaseModel):
    """Health check response for Tool servers."""
    status: str = Field(..., description="Health status: healthy, unhealthy, unknown")
    protocol_version: Optional[str] = None
    last_checked: Optional[datetime] = None
    error: Optional[str] = None


class DeployFromTemplateRequest(BaseModel):
    """Request to deploy from a template."""
    name: str
    env_vars: Optional[Dict[str, str]] = None


class ToolTemplate(BaseModel):
    """Pre-built Tool template information."""
    name: str
    version: str
    description: str
    category: Optional[str] = None
    tags: List[str] = []
    author: Optional[str] = None
    license: Optional[str] = None
    homepage: Optional[str] = None
    image: Optional[str] = None
    capabilities: List[str] = []
    required_env_vars: List[str] = []
    env_defaults: Dict[str, str] = {}
    risk_tier: int = 1
    verified: bool = False
    
    # Additional fields when imported as template
    id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    template_id: Optional[UUID] = None