"""Stable neutral ports for trusted registrars and resource semantics."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


ContractPayload = Mapping[str, object]


class _VersionedAdapter(Protocol):
    @property
    def adapter_id(self) -> str: ...


class WorkloadRegistrationAdapter(_VersionedAdapter, Protocol):
    """Map trusted deployment state to immutable workload registration."""

    async def reconcile_workload(
        self,
        deployment: ContractPayload,
    ) -> ContractPayload: ...


class ResourceRegistrationAdapter(_VersionedAdapter, Protocol):
    """Map an authorized descriptor to the protected-resource catalog."""

    async def reconcile_resource(
        self,
        descriptor: ContractPayload,
    ) -> ContractPayload: ...


class ResourceCanonicalizer(_VersionedAdapter, Protocol):
    """Canonicalize resource identity and exact request digest inputs."""

    def canonicalize(self, resource_id: object) -> str: ...

    def request_digest(self, request: ContractPayload) -> str: ...


class ResourceEntitlementAdapter(_VersionedAdapter, Protocol):
    """Evaluate current resource-specific subject entitlement."""

    def authorize(self, context: ContractPayload) -> bool: ...


class QuotaAdapter(_VersionedAdapter, Protocol):
    """Reserve resource-specific bounded quota dimensions."""

    def reserve(self, context: ContractPayload) -> ContractPayload: ...


class BrokerOperationAdapter(_VersionedAdapter, Protocol):
    """Execute one closed broker operation inside a trusted boundary."""

    async def execute(self, operation: ContractPayload) -> ContractPayload: ...


class SafeResultNormalizer(_VersionedAdapter, Protocol):
    """Normalize a resource result to a bounded credential-free payload."""

    def normalize(self, result: object) -> ContractPayload: ...


__all__ = (
    "BrokerOperationAdapter",
    "QuotaAdapter",
    "ResourceCanonicalizer",
    "ResourceEntitlementAdapter",
    "ResourceRegistrationAdapter",
    "SafeResultNormalizer",
    "WorkloadRegistrationAdapter",
)
