"""Disposable gate resources and teardown for the ENG-10096 live proof."""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Callable
from typing import Any

import pytest

from . import _mini_clearance as mc

logger = logging.getLogger(__name__)


def provision_gated_dataset(state: dict[str, Any]) -> None:
    """Create and bind a unique catalog row over the provisioner's exact CSV."""
    wheel = mc.wheel_and_index()
    if wheel is None:
        pytest.skip("gate-packages wheel/index not configured on the receiver")
    assert wheel is not None
    dataset_path = os.getenv("MINI_CLEARANCE_DATASET_PATH", "").strip()
    if not dataset_path:
        pytest.skip(
            "MINI_CLEARANCE_DATASET_PATH not set — set M5_TEST_KUBECTL for "
            "automatic provisioning or run tests.integration._gate_fixture provision"
        )

    receiver = state["receiver"]
    existing = next(
        (
            item
            for item in receiver.cluster.list_attributes()
            if item.name == "clearance"
        ),
        None,
    )
    state["clearance_prior_state"] = existing.state if existing else None
    state["installed_gate_package"] = not mc._already_installed(receiver)
    mc.declare_clearance_attribute(receiver)
    mc.install_gate_package(receiver, wheel[0], wheel[1])
    urn = receiver.datasets.create(
        name=f"eng10096-dataset-{uuid.uuid4().hex[:10]}",
        platform="file",
        properties={"path": dataset_path},
    )
    state["dataset_urn"] = urn
    receiver.datasets.set_gate(urn, type=mc.GATE_CLASSPATH, config={})


def _best_effort(label: str, action: Callable[[], Any]) -> None:
    try:
        action()
    except Exception as exc:  # pragma: no cover - teardown best-effort
        logger.warning("ENG-10096 cleanup failed for %s: %s", label, exc)


def _recover_guest_sub(state: dict[str, Any]) -> None:
    """Find an approval-created guest when claim failed before exposing its sub."""
    if state.get("guest_sub") or not state.get("external_id"):
        return
    try:
        body = state["receiver"]._request(
            "GET", f"/cluster/federations/{state['receiver_id']}/users"
        )
        rows = body if isinstance(body, list) else (body or {}).get("items", [])
        match = next(
            row
            for row in rows
            if row.get("linked_external_user") == state["external_id"]
        )
        state["guest_sub"] = str(match["external_id"])
    except Exception as exc:  # pragma: no cover - teardown best-effort
        logger.warning("ENG-10096 could not recover guest for cleanup: %s", exc)


def _remove_federation(client: Any, federation_id: str, side: str) -> None:
    _best_effort(
        f"{side} federation disconnect",
        lambda: client._request(
            "POST", f"/cluster/federations/{federation_id}/disconnect"
        ),
    )
    _best_effort(
        f"{side} federation delete",
        lambda: client._request("DELETE", f"/cluster/federations/{federation_id}"),
    )


def _restore_clearance_schema(state: dict[str, Any], receiver: Any) -> None:
    if "clearance_prior_state" not in state:
        return
    prior_state = state.get("clearance_prior_state")
    if prior_state != "declared":
        _best_effort(
            "clearance deprecate",
            lambda: receiver.cluster.deprecate_attribute("clearance"),
        )
    if prior_state not in {"declared", "deprecated"}:
        _best_effort(
            "clearance withdraw",
            lambda: receiver.cluster.withdraw_attribute("clearance"),
        )


def _remove_federations(state: dict[str, Any], initiator: Any, receiver: Any) -> None:
    for side, client in (("initiator", initiator), ("receiver", receiver)):
        fed_id = state.get(f"{side}_id")
        if fed_id:
            _remove_federation(client, str(fed_id), side)


def cleanup(state: dict[str, Any]) -> None:
    """Remove exact grants/bindings/resources, then disconnect and delete the pair."""
    receiver = state["receiver"]
    initiator = state["initiator"]
    _recover_guest_sub(state)
    if state.get("guest_sub") and state.get("dataset_urn"):
        _best_effort(
            "dataset viewer grant",
            lambda: receiver._request(
                "DELETE",
                "/authz/resources/dataset/grants",
                params={
                    "object_id": state["dataset_urn"],
                    "subject_namespace": "user",
                    "subject_id": state["guest_sub"],
                    "relation": "viewer",
                },
            ),
        )
    if state.get("dataset_urn"):
        _best_effort(
            "dataset gate", lambda: receiver.datasets.clear_gate(state["dataset_urn"])
        )
        _best_effort("dataset", lambda: receiver.datasets.delete(state["dataset_urn"]))
    if state.get("requester_created"):
        _best_effort(
            "requester",
            lambda: initiator.subjects.delete(state["username"], cascade_grants=True),
        )
    if state.get("installed_gate_package"):
        _best_effort(
            "gate package", lambda: receiver.gates.packages.uninstall("acme-gates")
        )
    _restore_clearance_schema(state, receiver)
    _remove_federations(state, initiator, receiver)
