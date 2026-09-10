"""State-safe setup helpers for the required two-cluster edge."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

from tests.integration import _mini_clearance as mc


@dataclass(frozen=True)
class PairIdentities:
    receiver_federation_id: str
    initiator_cluster_id: str
    receiver_cluster_id: str


def _delete_federation(client: Any, federation_id: str) -> None:
    client._request("DELETE", f"/cluster/federations/{federation_id}")


def _uninstall_owned_gate_package(receiver: Any) -> None:
    from kamiwaza_sdk.exceptions import APIError

    try:
        receiver.gates.packages.uninstall("acme-gates")
    except APIError as exc:
        if exc.status_code != 404:
            raise


def _installed_gate_package(receiver: Any) -> Any | None:
    """Read package ownership authoritatively; non-404 failures abort setup."""
    from kamiwaza_sdk.exceptions import APIError

    try:
        return receiver.gates.packages.get("acme-gates")
    except APIError as exc:
        if exc.status_code == 404:
            return None
        raise


def _assert_desired_gate_package(package: Any, wheel_dir: str) -> None:
    assert getattr(package, "package_spec", None) == mc.PACKAGE_SPEC
    assert getattr(package, "version", None) == "1.1.0"
    assert getattr(package, "hash_digest", None) == mc._wheel_sha256(wheel_dir)
    assert getattr(package, "status", None) == "active"
    assert mc.GATE_CLASSPATH in (getattr(package, "classpaths", None) or [])


def _ensure_gate_package(
    cleanup: ExitStack,
    receiver: Any,
    wheel_dir: str,
    index_url: str,
) -> None:
    """Install or validate the exact wheel with immediate owned cleanup."""
    package = _installed_gate_package(receiver)
    if package is None:
        result = receiver.gates.packages.install(
            mc.PACKAGE_SPEC,
            hash_digest=mc._wheel_sha256(wheel_dir),
            index_url=index_url,
        )
        package = result.package
        cleanup.callback(_uninstall_owned_gate_package, receiver)
        _assert_desired_gate_package(package, wheel_dir)
    else:
        _assert_desired_gate_package(package, wheel_dir)
    gate = receiver.gates.discover(mc.GATE_CLASSPATH)
    assert gate.name == mc.GATE_NAME


def provision_gated_dataset(
    cleanup: ExitStack,
    receiver: Any,
    prerequisites: Any,
    name: str,
) -> str:
    mc.declare_clearance_attribute(receiver)
    _ensure_gate_package(
        cleanup,
        receiver,
        prerequisites.wheel_dir,
        prerequisites.index_url,
    )
    urn = receiver.datasets.create(
        name=f"mini-clearance-{name}",
        platform="file",
        properties={"path": prerequisites.dataset_path},
    )
    cleanup.callback(receiver.datasets.delete, urn)
    receiver.datasets.set_gate(urn, type=mc.GATE_CLASSPATH, config={})
    return str(urn)


def pair_required_edge(
    cleanup: ExitStack,
    clients: Any,
    request: Any,
) -> PairIdentities:
    receiver_federation = clients.receiver.federations.pair(
        name=request.name,
        role="receiver",
        preshared_key=request.psk,
        **request.shared,
    )
    receiver_id = str(receiver_federation.id)
    cleanup.callback(_delete_federation, clients.receiver, receiver_id)
    initiator_federation = clients.initiator.federations.pair(
        name=request.name,
        role="initiator",
        remote_url=request.peer_url,
        preshared_key=request.psk,
        **request.shared,
    )
    initiator_id = str(initiator_federation.id)
    cleanup.callback(_delete_federation, clients.initiator, initiator_id)

    receiver_record = clients.receiver.federations.get(receiver_id)
    initiator_record = clients.initiator.federations.get(initiator_id)
    initiator_cluster_id = str(receiver_record.remote_cluster_id or "")
    receiver_cluster_id = str(initiator_record.remote_cluster_id or "")
    assert initiator_cluster_id, "receiver record has no initiator cluster identity"
    assert receiver_cluster_id, "initiator record has no receiver cluster identity"
    assert initiator_cluster_id != receiver_cluster_id, (
        "required federation edge resolved to one cluster identity: "
        f"{initiator_cluster_id}"
    )
    return PairIdentities(
        receiver_federation_id=receiver_id,
        initiator_cluster_id=initiator_cluster_id,
        receiver_cluster_id=receiver_cluster_id,
    )
