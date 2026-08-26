"""Cleanup handlers for owned shared-IdP federation fixtures."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Literal
from urllib.parse import quote

from kamiwaza_sdk.validation.federation_common import (
    optional_text,
    required_text,
)
from kamiwaza_sdk.validation.federation_state import owner_nonce
from kamiwaza_sdk.validation.inference_state import runtime_ownership_key
from kamiwaza_sdk.validation.models import CleanupResult, RuntimeContext


class CleanupContext:
    """Runtime handles and resource metadata needed by one cleanup handler."""

    def __init__(
        self,
        resources: Mapping[str, Any],
        receiver: Any,
        admin: Any,
        runtime: RuntimeContext,
    ) -> None:
        self.resources = resources
        self.receiver = receiver
        self.admin = admin
        self.runtime = runtime


def cleanup_mutation(mutation: Any, context: CleanupContext) -> CleanupResult:
    handler = CLEANUP_HANDLERS.get(mutation.resource_type)
    if handler is None:
        raise RuntimeError("unsupported fixture resource")
    handler(mutation, context)
    return CleanupResult(
        target_id=mutation.target_id,
        resource_type=mutation.resource_type,
        resource_id=mutation.resource_id,
        status="removed",
        detail=None,
    )


def cleanup_failure(mutation: Any, exc: Exception) -> CleanupResult:
    status: Literal["absent", "failed"] = (
        "absent" if getattr(exc, "status_code", None) == 404 else "failed"
    )
    return CleanupResult(
        target_id=mutation.target_id,
        resource_type=mutation.resource_type,
        resource_id=mutation.resource_id,
        status=status,
        detail=None if status == "absent" else f"{type(exc).__name__}: cleanup failed",
    )


def _cleanup_brokered_user(mutation: Any, context: CleanupContext) -> None:
    name = optional_text(context.resources, "federation_name")
    federation_id = optional_text(context.resources, "receiver_federation_id")
    if not name or not federation_id:
        raise RuntimeError("brokered-user cleanup locator is incomplete")
    external = _brokered_external_id(mutation, context.resources)
    context.receiver._request(
        "POST",
        f"/cluster/federations/{quote(federation_id, safe='')}/users/"
        f"{quote(external, safe='')}/revoke",
        params={"cancel_in_flight_jobs": "true"},
    )


def _brokered_external_id(mutation: Any, resources: Mapping[str, Any]) -> str:
    for key, value in resources.items():
        if key.startswith("brokered:") and value == mutation.resource_id:
            return str(value)
    return mutation.resource_id


def _cleanup_dataset(mutation: Any, context: CleanupContext) -> None:
    context.receiver.datasets.delete(mutation.resource_id)


def _cleanup_federation(mutation: Any, context: CleanupContext) -> None:
    context.receiver._request(
        "DELETE", f"/cluster/federations/{quote(mutation.resource_id, safe='')}"
    )


def _cleanup_execution_gate(mutation: Any, context: CleanupContext) -> None:
    del mutation
    previous = context.resources.get("previous_execution_gate")
    if previous is None:
        context.receiver.cluster.clear_execution_gate()
        return
    if not isinstance(previous, Mapping):
        raise RuntimeError("execution-gate cleanup snapshot is invalid")
    context.receiver.cluster.set_execution_gate(
        type=required_text(previous, "type"),
        config=previous.get("config") or {},
    )


def _cleanup_gate_package(mutation: Any, context: CleanupContext) -> None:
    context.receiver.gates.packages.uninstall(mutation.resource_id)


def _cleanup_model_deployment(mutation: Any, context: CleanupContext) -> None:
    serving = getattr(context.receiver, "serving", None)
    if serving is None or not callable(getattr(serving, "stop_deployment", None)):
        raise RuntimeError("model deployment cleanup service is unavailable")
    serving.stop_deployment(deployment_id=mutation.resource_id, force=True)


def _cleanup_keycloak_user(mutation: Any, context: CleanupContext) -> None:
    context.admin.delete_user(_cleanup_realm(context), mutation.resource_id)


def _cleanup_keycloak_client(mutation: Any, context: CleanupContext) -> None:
    context.admin.delete_client(_cleanup_realm(context), mutation.resource_id)


def _cleanup_keycloak_realm(mutation: Any, context: CleanupContext) -> None:
    realm = _cleanup_realm(context)
    nonce = owner_nonce(runtime_ownership_key(context.runtime), realm)
    context.admin.delete_owned_realm(realm, nonce)


def _cleanup_realm(context: CleanupContext) -> str:
    realm = optional_text(context.resources, "realm")
    if not realm:
        raise RuntimeError("keycloak cleanup realm is missing")
    return realm


_CleanupHandler = Callable[[Any, CleanupContext], None]
CLEANUP_HANDLERS: dict[str, _CleanupHandler] = {
    "brokered-user": _cleanup_brokered_user,
    "dataset": _cleanup_dataset,
    "receiver-federation": _cleanup_federation,
    "initiator-federation": _cleanup_federation,
    "execution-gate": _cleanup_execution_gate,
    "gate-package": _cleanup_gate_package,
    "model-deployment": _cleanup_model_deployment,
    "keycloak-user": _cleanup_keycloak_user,
    "keycloak-client": _cleanup_keycloak_client,
    "keycloak-realm": _cleanup_keycloak_realm,
}
