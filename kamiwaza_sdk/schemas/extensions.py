# kamiwaza_sdk/schemas/extensions.py

"""Pydantic models for the K8s-native extension API."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ExtensionPort(BaseModel):
    """Port exposed by an extension service container."""

    model_config = ConfigDict(extra="allow")

    name: Optional[str] = None
    container_port: int = Field(
        ..., ge=1, le=65535, description="Container port number"
    )
    protocol: Literal["TCP", "UDP"] = Field(
        default="TCP", description="Port protocol (TCP or UDP)"
    )


class ResourceSpec(BaseModel):
    """K8s resource requests and limits."""

    model_config = ConfigDict(extra="allow")

    requests: Optional[Dict[str, str]] = None
    limits: Optional[Dict[str, str]] = None


class ExtensionServiceSpec(BaseModel):
    """Specification for a single service within an extension."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(..., description="Service name")
    image: str = Field(..., description="Container image (registry/repo:tag)")
    primary: bool = Field(default=False, description="Primary ingress service")
    ports: List[ExtensionPort] = Field(default_factory=list)
    env: Optional[List[Dict[str, Any]]] = None
    replicas: int = Field(default=1, ge=0)
    resources: Optional[ResourceSpec] = None
    command: Optional[List[str]] = None
    args: Optional[List[str]] = None


class KamiwazaIntegrationSpec(BaseModel):
    """Kamiwaza platform integration settings."""

    model_config = ConfigDict(extra="allow")

    namespace: str = Field(default="kamiwaza")
    api_url: Optional[str] = None
    public_api_url: Optional[str] = None
    origin: Optional[str] = None
    use_auth: str = Field(default="true")


class NetworkingSpec(BaseModel):
    """Networking and ingress configuration."""

    model_config = ConfigDict(extra="allow")

    ingress_enabled: bool = Field(default=True)
    path_prefix: Optional[str] = None


class SecuritySpec(BaseModel):
    """Security classification."""

    model_config = ConfigDict(extra="allow")

    risk_tier: int = Field(default=1, ge=0, le=2)
    source_type: str = Field(default="kamiwaza")
    verified: bool = Field(default=False)


class CreateExtension(BaseModel):
    """Request to create a KamiwazaExtension CR."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(..., description="Extension name (K8s DNS label)")
    type: Literal["app", "tool"] = Field(
        ..., description="Extension type: 'app' or 'tool'"
    )
    version: str = Field(..., description="Extension version (semver)")
    services: List[ExtensionServiceSpec] = Field(..., min_length=1)
    kamiwaza: Optional[KamiwazaIntegrationSpec] = None
    networking: Optional[NetworkingSpec] = None
    security: Optional[SecuritySpec] = None


class ExtensionServiceStatus(BaseModel):
    """Observed status of a single service."""

    model_config = ConfigDict(extra="allow")

    name: str
    ready: bool = False
    replicas: int = 0
    available_replicas: int = 0
    message: Optional[str] = None


class ExtensionEndpoints(BaseModel):
    """Resolved endpoints for the extension."""

    model_config = ConfigDict(extra="allow")

    external: Optional[str] = None
    internal: Optional[str] = None


class Extension(BaseModel):
    """Extension response — maps from CR metadata + spec + status."""

    model_config = ConfigDict(extra="allow")

    name: str
    type: str
    version: str
    phase: Optional[str] = None
    services: List[ExtensionServiceStatus] = Field(default_factory=list)
    endpoints: Optional[ExtensionEndpoints] = None
    owner_user_id: Optional[str] = None
    created_at: Optional[datetime] = None
