"""Typed resource requests and responses for federated delegated jobs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Literal
from uuid import UUID

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import Specifier
from packaging.utils import InvalidName, canonicalize_name
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import Self

DatasetOperation = Literal["discover", "read", "retrieve"]
ModelOperation = Literal["discover", "chat"]

_DATASET_OPERATION_ORDER = {"discover": 0, "read": 1, "retrieve": 2}
_MODEL_OPERATION_ORDER = {"discover": 0, "chat": 1}
_MAX_RESOURCES_PER_KIND = 64
MAX_JOB_PYTHON_PACKAGES = 32


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


def _require_distinct(values: Sequence[object], message: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(message)


def normalize_python_packages(
    values: list[str] | tuple[str, ...] | None,
) -> tuple[str, ...]:
    """Normalize an SDK package list to Core's exact source-free coordinates."""
    if values is None:
        return ()
    if not isinstance(values, (list, tuple)):
        raise ValueError("python_packages must be a list")
    if len(values) > MAX_JOB_PYTHON_PACKAGES:
        raise ValueError("python_packages must contain at most 32 packages")
    normalized = tuple(_normalize_python_package(item) for item in values)
    names = [item.partition("==")[0] for item in normalized]
    _require_distinct(names, "python package names must be unique")
    return normalized


def _normalize_python_package(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("python packages must be strings")
    try:
        requirement = Requirement(value)
    except InvalidRequirement as exc:
        raise ValueError("invalid Python package coordinate") from exc
    specifiers = list(requirement.specifier)
    if _is_non_exact_requirement(requirement, specifiers):
        raise ValueError("Python packages require exact name==version coordinates")
    try:
        name = str(canonicalize_name(requirement.name, validate=True))
        version = str(Version(specifiers[0].version))
    except (InvalidName, InvalidVersion) as exc:
        raise ValueError("invalid Python package coordinate") from exc
    return f"{name}=={version}"


def _is_non_exact_requirement(
    requirement: Requirement, specifiers: Sequence[Specifier]
) -> bool:
    if requirement.url is not None:
        return True
    if requirement.extras:
        return True
    if requirement.marker is not None:
        return True
    if len(specifiers) != 1:
        return True
    specifier = specifiers[0]
    return getattr(specifier, "operator", None) != "==" or "*" in str(
        getattr(specifier, "version", "")
    )


__all__ = (
    "DatasetDelegatedAccess",
    "DatasetOperation",
    "DelegatedAccess",
    "GrantedDataset",
    "GrantedModel",
    "JobChatMessage",
    "MAX_JOB_PYTHON_PACKAGES",
    "ModelDelegatedAccess",
    "ModelOperation",
    "normalize_python_packages",
)
