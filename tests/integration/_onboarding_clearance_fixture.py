"""Disposable gate resources and teardown for the ENG-10096 live proof."""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Callable
from functools import partial
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
            "MINI_CLEARANCE_DATASET_PATH not set — run "
            "tests.integration._gate_fixture provision on the receiver"
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


def _recover_guest_subs(state: dict[str, Any]) -> None:
    """Find approval-created guests when claims failed before exposing subs."""
    external_ids = set(state.get("onboarding_external_ids", []))
    if state.get("external_id"):
        external_ids.add(str(state["external_id"]))
    if not external_ids:
        return
    try:
        body = state["receiver"]._request(
            "GET", f"/cluster/federations/{state['receiver_id']}/users"
        )
        rows = body if isinstance(body, list) else (body or {}).get("items", [])
        guest_subs = state.setdefault("dataset_guest_subs", [])
        known_subs = set(guest_subs)
        for row in rows:
            if row.get("linked_external_user") not in external_ids:
                continue
            guest_sub = str(row["external_id"])
            if guest_sub not in known_subs:
                guest_subs.append(guest_sub)
                known_subs.add(guest_sub)
    except Exception as exc:  # pragma: no cover - teardown best-effort
        logger.warning("ENG-10096 could not recover guests for cleanup: %s", exc)


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


def _remove_dataset_grants(state: dict[str, Any], receiver: Any) -> None:
    dataset_urn = state.get("dataset_urn")
    if not dataset_urn:
        return
    guest_subs = list(state.get("dataset_guest_subs", []))
    if state.get("guest_sub"):
        guest_subs.append(str(state["guest_sub"]))
    for guest_sub in dict.fromkeys(guest_subs):
        _best_effort(
            f"dataset viewer grant for {guest_sub}",
            partial(
                receiver._request,
                "DELETE",
                "/authz/resources/dataset/grants",
                params={
                    "object_id": dataset_urn,
                    "subject_namespace": "user",
                    "subject_id": guest_sub,
                    "relation": "viewer",
                },
            ),
        )


def _remove_dataset(state: dict[str, Any], receiver: Any) -> None:
    dataset_urn = state.get("dataset_urn")
    if not dataset_urn:
        return
    _best_effort("dataset gate", lambda: receiver.datasets.clear_gate(dataset_urn))
    _best_effort("dataset", lambda: receiver.datasets.delete(dataset_urn))


def _remove_requesters(state: dict[str, Any], initiator: Any) -> None:
    for requester in state.get("requester_clients", []):
        _best_effort("requester client", requester.close)
    requester_usernames = list(state.get("requester_usernames", []))
    if state.get("requester_created") and state.get("username"):
        requester_usernames.append(str(state["username"]))
    for username in dict.fromkeys(requester_usernames):
        _best_effort(
            f"requester {username}",
            partial(
                initiator.subjects.delete,
                username,
                cascade_grants=True,
            ),
        )


def _remove_gate_package(state: dict[str, Any], receiver: Any) -> None:
    if state.get("installed_gate_package"):
        _best_effort(
            "gate package", lambda: receiver.gates.packages.uninstall("acme-gates")
        )


def cleanup(state: dict[str, Any]) -> None:
    """Remove exact grants/bindings/resources, then disconnect and delete the pair."""
    receiver = state["receiver"]
    initiator = state["initiator"]
    _recover_guest_subs(state)
    _remove_dataset_grants(state, receiver)
    _remove_dataset(state, receiver)
    _remove_requesters(state, initiator)
    _remove_gate_package(state, receiver)
    _restore_clearance_schema(state, receiver)
    _remove_federations(state, initiator, receiver)
