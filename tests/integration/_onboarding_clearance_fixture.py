"""Disposable gate resources and teardown for the ENG-10096 live proof."""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from functools import partial
from typing import Any, Iterator

import pytest

from . import _mini_clearance as mc

logger = logging.getLogger(__name__)

_CLEARANCE_SCHEMA_PATH = "/cluster/attribute-schema/clearance"


class _CleanupReport:
    """Attempt every owned cleanup action, then fail on any incomplete phase."""

    def __init__(self) -> None:
        self._failures: list[tuple[str, Exception]] = []

    def attempt(self, label: str, action: Callable[[], Any]) -> None:
        try:
            action()
        except Exception as exc:  # pragma: no cover - live teardown failure
            self._failures.append((label, exc))
            logger.warning("ENG-10096 cleanup failed for %s", label, exc_info=True)

    def raise_if_failed(self) -> None:
        if not self._failures:
            return
        labels = ", ".join(label for label, _error in self._failures)
        raise AssertionError(f"ENG-10096 cleanup failed for: {labels}") from (
            self._failures[0][1]
        )


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


def _recover_guest_subs(state: dict[str, Any]) -> None:
    """Find approval-created guests when claims failed before exposing subs."""
    external_ids = set(state.get("onboarding_external_ids", []))
    if state.get("external_id"):
        external_ids.add(str(state["external_id"]))
    if not external_ids:
        return
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


def _remove_federation(
    client: Any,
    federation_id: str,
    side: str,
    report: _CleanupReport,
) -> None:
    report.attempt(
        f"{side} federation disconnect",
        lambda: client._request(
            "POST", f"/cluster/federations/{federation_id}/disconnect"
        ),
    )
    report.attempt(
        f"{side} federation delete",
        lambda: client._request("DELETE", f"/cluster/federations/{federation_id}"),
    )


def _restore_clearance_schema(state: dict[str, Any], receiver: Any) -> None:
    if "clearance_prior_state" not in state:
        return
    prior_state = state.get("clearance_prior_state")
    if prior_state == "declared":
        _assert_clearance_schema_state(receiver, prior_state)
        return
    if prior_state == "deprecated":
        response = receiver._request("DELETE", _CLEARANCE_SCHEMA_PATH)
        expected_response_state = "deprecated"
    elif prior_state is None:
        # The fixture never writes ``clearance`` to native-realm subjects;
        # receiver guest values live in the federation-scoped realm instead.
        # The Core audit count for this native schema transition is exactly 0.
        response = receiver._request(
            "DELETE",
            _CLEARANCE_SCHEMA_PATH,
            params={"force": "true", "subjects_holding_value": 0},
        )
        expected_response_state = "withdrawn"
    else:
        raise AssertionError("clearance schema had an unsupported prior state")
    if not isinstance(response, dict):
        raise AssertionError("clearance schema cleanup returned a non-object response")
    if response.get("state") != expected_response_state:
        raise AssertionError(
            "clearance schema cleanup returned an unexpected transition state"
        )
    _assert_clearance_schema_state(receiver, prior_state)


def _assert_clearance_schema_state(receiver: Any, expected: Any) -> None:
    current = next(
        (
            item.state
            for item in receiver.cluster.list_attributes()
            if item.name == "clearance"
        ),
        None,
    )
    if current != expected:
        raise AssertionError("clearance schema cleanup did not restore its prior state")


def _remove_federations(
    state: dict[str, Any],
    initiator: Any,
    receiver: Any,
    report: _CleanupReport,
) -> None:
    for side, client in (("initiator", initiator), ("receiver", receiver)):
        fed_id = state.get(f"{side}_id")
        if fed_id:
            _remove_federation(client, str(fed_id), side, report)


def _remove_dataset_grants(
    state: dict[str, Any], receiver: Any, report: _CleanupReport
) -> None:
    dataset_urn = state.get("dataset_urn")
    if not dataset_urn:
        return
    guest_subs = list(state.get("dataset_guest_subs", []))
    if state.get("guest_sub"):
        guest_subs.append(str(state["guest_sub"]))
    for guest_sub in dict.fromkeys(guest_subs):
        report.attempt(
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


def _remove_dataset(
    state: dict[str, Any], receiver: Any, report: _CleanupReport
) -> None:
    dataset_urn = state.get("dataset_urn")
    if not dataset_urn:
        return
    report.attempt("dataset gate", lambda: receiver.datasets.clear_gate(dataset_urn))
    report.attempt("dataset", lambda: receiver.datasets.delete(dataset_urn))


def _remove_requesters(
    state: dict[str, Any], initiator: Any, report: _CleanupReport
) -> None:
    for requester in state.get("requester_clients", []):
        report.attempt("requester client", requester.close)
    requester_usernames = list(state.get("requester_usernames", []))
    if state.get("requester_created") and state.get("username"):
        requester_usernames.append(str(state["username"]))
    for username in dict.fromkeys(requester_usernames):
        report.attempt(
            f"requester {username}",
            partial(
                initiator.subjects.delete,
                username,
                cascade_grants=True,
            ),
        )


def _remove_gate_package(
    state: dict[str, Any], receiver: Any, report: _CleanupReport
) -> None:
    if state.get("installed_gate_package"):
        report.attempt(
            "gate package", lambda: receiver.gates.packages.uninstall("acme-gates")
        )


def cleanup(state: dict[str, Any]) -> None:
    """Remove exact grants/bindings/resources, then disconnect and delete the pair."""
    receiver = state["receiver"]
    initiator = state["initiator"]
    report = _CleanupReport()
    phases = (
        ("guest recovery", partial(_recover_guest_subs, state)),
        (
            "dataset grants",
            partial(_remove_dataset_grants, state, receiver, report),
        ),
        ("dataset", partial(_remove_dataset, state, receiver, report)),
        ("requesters", partial(_remove_requesters, state, initiator, report)),
        ("gate package", partial(_remove_gate_package, state, receiver, report)),
        ("clearance schema", partial(_restore_clearance_schema, state, receiver)),
        (
            "federations",
            partial(_remove_federations, state, initiator, receiver, report),
        ),
    )
    for label, action in phases:
        report.attempt(label, action)
    report.raise_if_failed()


@contextmanager
def cleanup_preserving_primary(state: dict[str, Any]) -> Iterator[None]:
    """Fail on teardown alone without replacing an active test/setup failure."""
    primary_error: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            cleanup(state)
        except Exception:
            if primary_error is None:
                raise
            logger.exception(
                "ENG-10096 cleanup failed while preserving the primary failure"
            )
