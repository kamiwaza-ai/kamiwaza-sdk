# kamiwaza_client/schemas/serving/serving.py

from pydantic import BaseModel, Field
from typing import List, Optional
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

class ModelDeployment(CreateModelDeployment):
    id: UUID = Field(description="The UUID of the deployment")
    requested_at: datetime = Field(description="Time at which the deployment was requested")
    deployed_at: Optional[datetime] = Field(default=None, description="Time at which the deployment was started")
    serve_path: Optional[str] = Field(default=None, description="Ray serve path prefix of the deployment")
    single_node_mode: Optional[bool] = Field(default=False, description="Whether the deployment is in single node mode")
    status: str = Field(description="Status of the deployment")
    instances: List['ModelInstance'] = Field(default_factory=list, description="List of instances associated with the deployment")

class ModelInstance(BaseModel):
    id: UUID = Field(description="The UUID of the instance")
    deployment_id: UUID = Field(description="The UUID of the deployment")
    deployed_at: datetime = Field(description="Time at which the instance was deployed")
    container_id: Optional[str] = Field(default=None, description="Container ID of the instance")
    node_id: Optional[UUID] = Field(default=None, description="Node ID where the instance is running")
    host_name: Optional[str] = Field(default=None, description="Name of the host")
    listen_port: Optional[int] = Field(default=None, description="Port on which the instance is listening")
    status: Optional[str] = Field(default=None, description="Status of the instance")

class UIModelDeployment(ModelDeployment):
    m_name: Optional[str] = Field(default=None, description="Name of the model")
    host_ip: Optional[str] = Field(default=None, description="IP address associated with the host_name")