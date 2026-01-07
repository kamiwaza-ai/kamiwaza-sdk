# kamiwaza_sdk/schemas/serving/serving.py

from pydantic import BaseModel, Field
from typing import Dict, List, Optional
from datetime import datetime
from uuid import UUID

class CreateModelDeployment(BaseModel):
    m_id: UUID = Field(description="The UUID of the model to deploy")
    m_file_id: Optional[UUID] = Field(default=None, description="Which weights file to use for models with >1 set of weights")
    m_config_id: UUID = Field(description="The UUID of the ModelConfig to use for this deployment")
    engine_name: Optional[str] = Field(default=None, description="Name of the engine to use for deployment")
    duration: Optional[int] = Field(default=None, description="Duration in minutes for which the model should be deployed")
    min_copies: int = Field(default=1, description="Minimum number of copies to maintain")
    starting_copies: int = Field(default=1, description="Number of copies to start with")
    max_copies: Optional[int] = Field(default=None, description="Maximum number of copies allowed")
    location: Optional[str] = Field(default=None, description="Location where the model is to be deployed")
    lb_port: int = Field(default=0, description="Port on which the load balancer is listening")
    autoscaling: bool = Field(default=False, description="Whether autoscaling is enabled")
    force_cpu: bool = Field(default=False, description="Whether to force CPU usage")
    node_resource_type: Optional[str] = Field(default=None, description="The specialized gpu node resource")
    max_concurrent_requests: Optional[int] = Field(default=None, description="Maximum number of concurrent requests allowed")
    vram_allocation: Optional[float] = Field(default=None, description="The VRAM allocation, in bytes of vram for each copy of the deployed model")
    gpu_allocation: Optional[float] = Field(default=None, description="The GPU allocation, as a percentage of the total VRAM available")

    def __str__(self):
        return (
            f"CreateModelDeployment:\n"
            f"Model ID: {self.m_id}\n"
            f"Config ID: {self.m_config_id}\n"
            f"Copies: {self.starting_copies}"
        )

    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())

class ModelInstance(BaseModel):
    id: UUID = Field(description="The UUID of the instance")
    deployment_id: UUID = Field(description="The UUID of the deployment")
    deployed_at: datetime = Field(description="Time at which the instance was deployed")
    container_id: Optional[str] = Field(default=None, description="Container ID of the instance")
    node_id: Optional[UUID] = Field(default=None, description="Node ID where the instance is running")
    host_name: Optional[str] = Field(default=None, description="Name of the host")
    listen_port: Optional[int] = Field(default=None, description="Port on which the instance is listening")
    status: Optional[str] = Field(default=None, description="Status of the instance")

    def __str__(self):
        return (
            f"ModelInstance:\n"
            f"ID: {self.id}\n"
            f"Deployment ID: {self.deployment_id}\n"
            f"Status: {self.status}\n"
            f"Listen Port: {self.listen_port}"
        )

    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())

class ModelDeployment(CreateModelDeployment):
    id: UUID = Field(description="The UUID of the deployment")
    requested_at: datetime = Field(description="Time at which the deployment was requested")
    deployed_at: Optional[datetime] = Field(default=None, description="Time at which the deployment was started")
    serve_path: Optional[str] = Field(default=None, description="Ray serve path prefix of the deployment")
    access_path_prefix: Optional[str] = Field(
        default=None,
        description="Public path prefix used for ingress routing (e.g., /models)",
    )
    access_path: Optional[str] = Field(
        default=None,
        description="Full public path for this deployment (e.g., /models/{uuid})",
    )
    single_node_mode: Optional[bool] = Field(default=False, description="Whether the deployment is in single node mode")
    status: str = Field(description="Status of the deployment")
    instances: List[ModelInstance] = Field(default_factory=list, description="List of instances associated with the deployment")

    def __str__(self):
        return (
            f"ModelDeployment: ID: {self.id}\n"
            f"Model ID: {self.m_id}\n"
            f"Status: {self.status}\n"
            f"Instances: {len(self.instances)}\n"
            f"Requested at: {self.requested_at}"
        )

    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())

class UIModelDeployment(ModelDeployment):
    m_name: Optional[str] = Field(default=None, description="Name of the model")
    host_ip: Optional[str] = Field(default=None, description="IP address associated with the host_name")

    def __str__(self):
        return (
            f"UIModelDeployment: ID: {self.id}\n"
            f"Model Name: {self.m_name}\n"
            f"Status: {self.status}\n"
            f"Instances: {len(self.instances)}\n"
            f"Host IP: {self.host_ip}"
        )

    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())



class ActiveModelDeployment(BaseModel):
    id: UUID = Field(description="The UUID of the deployment")
    m_id: UUID = Field(description="The UUID of the model")
    m_name: str = Field(description="Name of the model")
    status: str = Field(description="Status of the deployment")
    instances: List[ModelInstance] = Field(description="List of active instances")
    lb_port: int = Field(description="Load balancer port for the deployment")
    endpoint: Optional[str] = Field(
        default=None, 
        description="The OpenAI-compatible endpoint URL for this deployment"
    )

    @property
    def is_available(self) -> bool:
        """Check if deployment has at least one running instance"""
        return any(i.status == "DEPLOYED" for i in self.instances)

    @property
    def active_instance(self) -> Optional[ModelInstance]:
        """Get first active instance if any exists"""
        return next((i for i in self.instances if i.status == "DEPLOYED"), None)


class ContainerLogResponse(BaseModel):
    deployment_id: UUID = Field(description="The UUID of the deployment")
    engine_type: Optional[str] = Field(default=None, description="Engine type (vllm, llamacpp, etc.)")
    container_id: Optional[str] = Field(default=None, description="Container ID if available")
    log_file_path: str = Field(description="Path to the aggregated log file")
    logs: List[str] = Field(description="Captured log lines")
    total_lines_seen: int = Field(description="Total number of lines observed")
    current_lines_stored: int = Field(description="Number of lines currently stored")
    compressed: bool = Field(description="Whether capture truncated to head/tail")
    capture_active: bool = Field(description="Whether capture is still running")


class ContainerLogPatternResponse(BaseModel):
    deployment_id: UUID = Field(description="The UUID of the deployment")
    patterns_detected: Dict[str, bool] = Field(description="Map of pattern name to detection status")
    analysis_timestamp: datetime = Field(description="Timestamp of the most recent analysis")
    failure_lines: Optional[List[Dict[str, str]]] = Field(
        default=None,
        description="Log lines where failures were detected",
    )


class ContainerLogListResponse(BaseModel):
    engine_type: str = Field(description="Engine type (vllm, llamacpp, etc.)")
    logs: List[Dict[str, str]] = Field(description="Available log entries with metadata")
