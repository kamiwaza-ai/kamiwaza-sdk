"""Typed resource requests and responses for federated delegated jobs."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import Self

DatasetOperation = Literal["discover", "read", "retrieve"]
ModelOperation = Literal["discover", "chat"]

_DATASET_OPERATION_ORDER = {"discover": 0, "read": 1, "retrieve": 2}
_MODEL_OPERATION_ORDER = {"discover": 0, "chat": 1}
_MAX_RESOURCES_PER_KIND = 64


class _ExactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DatasetDelegatedAccess(_ExactRequest):
    """One exact receiver dataset and the operations requested for it."""

    urn: Annotated[str, Field(min_length=1)]
    operations: Annotated[tuple[DatasetOperation, ...], Field(min_length=1)]

    @field_validator("urn")
    @classmethod
    def validate_urn(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith("urn:li:dataset:") or "*" in normalized:
            raise ValueError("dataset urn must be exact")
        return normalized

    @field_validator("operations")
    @classmethod
    def normalize_operations(
        cls, value: tuple[DatasetOperation, ...]
    ) -> tuple[DatasetOperation, ...]:
        if len(set(value)) != len(value):
            raise ValueError("dataset operations must be unique")
        return tuple(sorted(value, key=_DATASET_OPERATION_ORDER.__getitem__))


class ModelDelegatedAccess(_ExactRequest):
    """One exact receiver model deployment and the requested operations."""

    deployment_id: UUID
    operations: Annotated[tuple[ModelOperation, ...], Field(min_length=1)]

    @field_validator("operations")
    @classmethod
    def normalize_operations(
        cls, value: tuple[ModelOperation, ...]
    ) -> tuple[ModelOperation, ...]:
        if len(set(value)) != len(value):
            raise ValueError("model operations must be unique")
        return tuple(sorted(value, key=_MODEL_OPERATION_ORDER.__getitem__))


class DelegatedAccess(_ExactRequest):
    """Closed resource vocabulary for a receiver-executed job."""

    datasets: Annotated[
        tuple[DatasetDelegatedAccess, ...], Field(max_length=_MAX_RESOURCES_PER_KIND)
    ] = ()
    models: Annotated[
        tuple[ModelDelegatedAccess, ...], Field(max_length=_MAX_RESOURCES_PER_KIND)
    ] = ()

    @model_validator(mode="after")
    def normalize_resources(self) -> Self:
        if not self.datasets and not self.models:
            raise ValueError("delegated_access must name at least one resource")
        _require_distinct(
            [resource.urn for resource in self.datasets],
            "dataset resources must be unique",
        )
        _require_distinct(
            [resource.deployment_id for resource in self.models],
            "model resources must be unique",
        )
        self.datasets = tuple(sorted(self.datasets, key=lambda item: item.urn))
        self.models = tuple(
            sorted(self.models, key=lambda item: str(item.deployment_id))
        )
        return self


class GrantedDataset(BaseModel):
    """Dataset metadata returned by the local credential agent."""

    model_config = ConfigDict(extra="allow", frozen=True)

    dataset_id: str
    name: str


class GrantedModel(BaseModel):
    """Model metadata returned by the local credential agent."""

    model_config = ConfigDict(extra="allow", frozen=True)

    deployment_id: UUID
    model_id: UUID
    name: str


class JobChatMessage(_ExactRequest):
    """One bounded chat message sent through the local agent."""

    role: Literal["system", "user", "assistant"]
    content: Annotated[str, Field(min_length=1, max_length=32 * 1024)]


def _require_distinct(values: list[object], message: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(message)


__all__ = (
    "DatasetDelegatedAccess",
    "DatasetOperation",
    "DelegatedAccess",
    "GrantedDataset",
    "GrantedModel",
    "JobChatMessage",
    "ModelDelegatedAccess",
    "ModelOperation",
)
