# kamiwaza_sdk/schemas/serving/serving.py

import re
from decimal import Decimal, InvalidOperation, localcontext

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator
from typing import Dict, List, Literal, Optional
from datetime import datetime
from uuid import UUID


class CpuResourceQuantities(BaseModel):
    """Explicit Kubernetes CPU request quantities for tenant inference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cpu: StrictStr = Field(min_length=1, max_length=128)
    memory: StrictStr = Field(min_length=1, max_length=128)

    @field_validator("cpu")
    @classmethod
    def _valid_cpu_quantity(cls, value: str) -> str:
        match = re.fullmatch(
            r"([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))([numkMGTPE]|[eE][+-]?[0-9]+)?",
            value,
        )
        if match is None:
            raise ValueError("cpu must be a Kubernetes decimal quantity")
        try:
            number, suffix = match.groups()
            exponent = {None: 0, "n": -9, "u": -6, "m": -3, "k": 3, "M": 6, "G": 9, "T": 12, "P": 15, "E": 18}.get(suffix)
            exponent = int(suffix[1:]) if exponent is None else exponent
            with localcontext() as context:
                context.prec = 256
                amount = Decimal(number) * (Decimal(10) ** (exponent + 3))
        except (InvalidOperation, ValueError, OverflowError) as exc:
            raise ValueError("cpu exceeds the supported quantity range") from exc
        if amount <= 0 or amount > 2**63 - 1 or amount != amount.to_integral_value():
            raise ValueError("cpu must fit a positive signed 64-bit integer")
        return value

    @field_validator("memory")
    @classmethod
    def _valid_memory_quantity(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?(?:Mi|Gi)", value):
            raise ValueError("memory must use a positive Mi or Gi quantity")
        number, unit = value[:-2], value[-2:]
        with localcontext() as context:
            context.prec = 256
            amount = Decimal(number) * (1024 ** (2 if unit == "Mi" else 3))
        if amount <= 0 or amount > 2**63 - 1 or amount != amount.to_integral_value():
            raise ValueError("memory must fit a positive signed 64-bit integer")
        return value


class CpuResourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    architecture: StrictStr = Field(min_length=1)
    requests: CpuResourceQuantities

    @field_validator("architecture")
    @classmethod
    def _no_whitespace_architecture(cls, value: str) -> str:
        if not value.strip() or any(char.isspace() for char in value):
            raise ValueError("architecture must be a non-empty identifier")
        return value


class CpuRuntimeSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selection: Literal["automatic"]


class CpuInferenceRequest(BaseModel):
    """Provider-neutral CPU request accepted by the restricted tenant path."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_version: StrictInt = Field(alias="schemaVersion")
    cpu: CpuResourceRequest
    runtime: CpuRuntimeSelection

    @field_validator("schema_version")
    @classmethod
    def _schema_v1(cls, value: int) -> int:
        if value != 1:
            raise ValueError("schemaVersion must be 1")
        return value

class CreateModelDeployment(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

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
    inference_resources: Optional[CpuInferenceRequest] = Field(
        default=None,
        alias="inferenceResources",
        description="Explicit CPU resources for restricted tenant inference.",
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_resource_alias_collision(cls, value):
        if isinstance(value, dict) and "inferenceResources" in value and "inference_resources" in value:
            raise ValueError("inferenceResources and inference_resources may not both be supplied")
        return value

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
    last_error_code: Optional[str] = Field(
        default=None,
        description="Short error code for the last failure (e.g., OOM, CUDA_ERROR, MODEL_LOADING_FAILURE, CONTAINER_EXITED)",
    )
    last_error_message: Optional[str] = Field(
        default=None, description="Last error message/explanation"
    )
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
    engine_name: Optional[str] = Field(
        default=None, description="Inference engine used by the deployment"
    )
    m_file_id: Optional[UUID] = Field(
        default=None, description="Selected model weights file"
    )
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
