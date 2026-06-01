# kamiwaza_sdk/schemas/extensions.py

"""Pydantic models for the K8s-native extension API."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ExtensionPort(BaseModel):
    """Port exposed by an extension service container."""

    # serialize_by_alias keeps ``appProtocol`` camelCase in the JSON payload
    # even when the parent (CreateExtension / patch payloads) is dumped with
    # default model_dump(). Without it the K8s API server sees the unknown
    # ``app_protocol`` key and drops the field.
    model_config = ConfigDict(
        extra="allow", populate_by_name=True, serialize_by_alias=True
    )

    name: Optional[str] = None
    container_port: int = Field(
        ..., ge=1, le=65535, description="Container port number"
    )
    protocol: Literal["TCP", "UDP"] = Field(
        default="TCP", description="Port protocol (TCP or UDP)"
    )
    # L7 protocol hint. Mirrors corev1.ServicePort.appProtocol; istio reads
    # this to pick the right L7 filter (grpc, http, http2) instead of falling
    # back to name-prefix sniffing.
    app_protocol: Optional[str] = Field(
        default=None,
        alias="appProtocol",
        description="Application protocol hint (e.g., http, http2, grpc)",
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
    tls_reject_unauthorized: Optional[str] = None


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
    type: Literal["app", "tool", "service"] = Field(
        ..., description="Extension type: 'app', 'tool', or 'service'"
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


# ---------------------------------------------------------------------------
# Patch / update models
# ---------------------------------------------------------------------------


class ImagePatch(BaseModel):
    """Image update — typically only tag changes during dev."""

    model_config = ConfigDict(extra="allow")

    tag: str
    registry: Optional[str] = None
    repository: Optional[str] = None


class PatchServiceSpec(BaseModel):
    """Partial service update — only name is required (for matching)."""

    model_config = ConfigDict(extra="allow")

    name: str
    image: Optional[ImagePatch] = None
    env: Optional[List[Dict[str, Any]]] = None
    replicas: Optional[int] = Field(None, ge=0)


class PatchExtension(BaseModel):
    """Partial extension update. Only service-level fields supported in v1."""

    model_config = ConfigDict(extra="allow")

    services: List[PatchServiceSpec] = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Detailed status models
# ---------------------------------------------------------------------------


class PodInfo(BaseModel):
    """Individual pod information for logs/shell targeting."""

    model_config = ConfigDict(extra="allow")

    name: str
    phase: str
    ready: bool
    restart_count: int = 0
    started_at: Optional[datetime] = None


class ServiceStatusDetail(BaseModel):
    """Detailed per-service status with pod-level info."""

    model_config = ConfigDict(extra="allow")

    name: str
    image_tag: str
    ready_replicas: int = 0
    replicas: int = 0
    restart_count: int = 0
    pods: List[PodInfo] = []


class ExtensionEvent(BaseModel):
    """Recent K8s event relevant to the extension."""

    model_config = ConfigDict(extra="allow")

    type: str
    reason: str
    message: str
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    count: int = 1


class ExtensionStatus(BaseModel):
    """Rich deployment status response."""

    model_config = ConfigDict(extra="allow")

    name: str
    phase: str
    url: Optional[str] = None
    services: List[ServiceStatusDetail] = []
    rolling_update: bool = False
    events: List[ExtensionEvent] = []
