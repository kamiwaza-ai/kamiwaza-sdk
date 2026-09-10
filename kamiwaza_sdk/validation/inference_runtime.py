"""Runtime seam used by the SDK-owned local inference scenario."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from kamiwaza_sdk.validation.models import RuntimeCluster


@dataclass(frozen=True)
class CatalogFile:
    """One catalog file reduced to the fields the scenario must prove."""

    file_id: str
    name: str
    ready: bool


@dataclass(frozen=True)
class CatalogModel:
    """Exact repository and its currently observable files."""

    model_id: str | None
    repository: str
    files: tuple[CatalogFile, ...]


@dataclass(frozen=True)
class CatalogConfig:
    """One deployable model configuration."""

    config_id: str
    default: bool


@dataclass(frozen=True)
class SelectedModel:
    """Deterministically selected deployment inputs."""

    model_id: str
    config_id: str
    model_file_id: str | None
    model_files: tuple[CatalogFile, ...]


@dataclass(frozen=True)
class DeploymentRequest:
    """Product-semantic request; it intentionally contains no engine CLI flags."""

    model_id: str
    config_id: str
    model_file_id: str | None
    engine: str
    runtime_profile: str


@dataclass(frozen=True)
class ReadyDeployment:
    """API-observable terminal deployment state."""

    engine: str
    instance_count: int


@dataclass(frozen=True)
class RuntimeObservation:
    """Actual pod image and command after the product adapter resolves them."""

    image_digest: str | None
    effective_args: tuple[str, ...]


class InferenceCluster(Protocol):
    """Minimal cluster operations owned by the inference scenario."""

    def discover(self, repository: str) -> CatalogModel: ...

    def ensure_download(self, repository: str, quantization: str) -> CatalogModel: ...

    def list_configs(self, model_id: str) -> tuple[CatalogConfig, ...]: ...

    def deploy(self, request: DeploymentRequest) -> str: ...

    def wait_ready(self, deployment_id: str) -> ReadyDeployment: ...

    def observe_runtime(
        self, deployment_id: str, engine: str
    ) -> RuntimeObservation: ...

    def chat(self, deployment_id: str, messages: tuple[dict[str, str], ...]) -> str: ...

    def stop(self, deployment_id: str) -> bool: ...

    def is_active(self, deployment_id: str) -> bool: ...

    def close(self) -> None: ...


class InferenceClusterFactory(Protocol):
    """Create a cluster client from opaque runtime references."""

    def __call__(self, runtime_cluster: RuntimeCluster) -> InferenceCluster: ...
