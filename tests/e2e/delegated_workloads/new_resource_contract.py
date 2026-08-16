"""Authorized resource-registration adapter for the neutral E2E platform."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from kamiwaza_sdk.delegated_workloads import ResourceRegistrationAdapter


RESOURCE_REVISION_ID = UUID("77777777-7777-4777-8777-777777777777")


class NeutralResourceRegistrar:
    """Activate only the exact staged conformance descriptor."""

    @property
    def adapter_id(self) -> str:
        return "neutral-platform-resource-registrar:v1"

    async def reconcile_resource(
        self,
        descriptor: Mapping[str, object],
    ) -> Mapping[str, object]:
        _require_descriptor(descriptor)
        return {
            **descriptor,
            "status": "active",
            "revision_id": str(RESOURCE_REVISION_ID),
            "registrar_adapter_id": self.adapter_id,
        }


def _require_descriptor(descriptor: Mapping[str, object]) -> None:
    actions = descriptor.get("actions")
    if not isinstance(actions, Mapping):
        raise ValueError("resource descriptor is invalid")
    checks = (
        descriptor.get("resource_type") == "conformance.document",
        descriptor.get("descriptor_version") == "v1",
        descriptor.get("status") == "staged",
        _classification(actions, "read") == ("read", "none"),
        _classification(actions, "mutate")
        == ("mutation", "exact_it_approval"),
        descriptor.get("guard_contract_version") == "guard:v1",
    )
    if not all(checks):
        raise ValueError("resource descriptor is invalid")


def _classification(
    actions: Mapping[str, object],
    name: str,
) -> tuple[object, object]:
    action = actions.get(name)
    if not isinstance(action, Mapping):
        return None, None
    return action.get("effect_class"), action.get("approval_class")


def registrar_adapter() -> ResourceRegistrationAdapter:
    """Return the neutral implementation typed through the public SDK port."""
    return NeutralResourceRegistrar()


__all__ = ("RESOURCE_REVISION_ID", "registrar_adapter")
