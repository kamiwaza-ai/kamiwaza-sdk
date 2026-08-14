"""ENG-10096 — receiver-assigned onboarding clearance reaches a mesh gate.

The receiver approves one self-service guest with ``clearance=S`` and a viewer
grant on a MiniClearanceGate dataset. The requester claims the receiver-realm
credential, presents it on the mesh retrieval, and must receive exactly the
known-answer U/S rows plus the filtered audit footer.

``MINI_CLEARANCE_DATASET_PATH`` is the receiver-visible CSV written from
``mini_clearance_records.json`` by ``tests.integration._gate_fixture``. The test
creates its own dataset catalog row and gate binding; it never owns or removes
that shared provisioner fixture.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from kamiwaza_sdk import KamiwazaClient

from . import _mini_clearance as mc
from . import _onboarding_clearance_fixture as fixture_support
from .test_federation_user_onboarding_live import (
    _claim,
    _decode_jwt_payload,
    _obj,
    _onboarding_path,
    _receiver_request_id,
    _self_request_onboarding,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
    pytest.mark.requires_two_clusters,
]


def _provision_pair(state: dict[str, Any], live_peer_base_url: str) -> None:
    initiator = state["initiator"]
    receiver = state["receiver"]
    name = f"eng10096-clearance-{uuid.uuid4().hex[:10]}"
    psk = uuid.uuid4().hex
    receiver_fed = receiver.federations.pair(
        name=name,
        role="receiver",
        preshared_key=psk,
        realm_scope="per_federation",
    )
    state.update(name=name, receiver_id=str(receiver_fed.id))
    initiator_fed = initiator.federations.pair(
        name=name,
        role="initiator",
        remote_url=live_peer_base_url,
        preshared_key=psk,
        realm_scope="per_federation",
    )
    state["initiator_id"] = str(initiator_fed.id)


def _provision_requester(state: dict[str, Any], live_base_url: str) -> KamiwazaClient:
    username = f"eng10096-guest-{uuid.uuid4().hex[:10]}"
    state["username"] = username
    state["initiator"].subjects.upsert(username, attributes={}, password=username)
    state["requester_created"] = True
    return mc.authed_client(live_base_url, username, username, verify=False)


def _approve_and_claim(state: dict[str, Any], requester: KamiwazaClient) -> str:
    status = _self_request_onboarding(
        requester,
        state["initiator_id"],
        "ENG-10096 receiver-assigned clearance gate proof.",
    )
    assert status.get("status") == "REQUESTED", f"unexpected status: {status!r}"
    claim_token = status.get("claim_token")
    external_id = status.get("external_id")
    assert claim_token, f"self-service request returned no claim token: {status!r}"
    assert external_id, f"self-service request returned no external id: {status!r}"
    state["external_id"] = str(external_id)

    request_id = _receiver_request_id(
        state["receiver"], state["receiver_id"], str(external_id)
    )
    approved = _obj(
        state["receiver"],
        "POST",
        _onboarding_path(state["receiver_id"], f"/{request_id}/approve"),
        json={
            "attributes": {"clearance": "S"},
            "relations": [
                {
                    "relation": "viewer",
                    "object": f"dataset:{state['dataset_urn']}",
                }
            ],
        },
    )
    assert approved.get("status") == "APPROVED", f"approve failed: {approved!r}"
    mine = _obj(
        requester,
        "GET",
        _onboarding_path(state["initiator_id"], "/me"),
    )
    assert mine.get("status") == "APPROVED", f"approval did not propagate: {mine!r}"
    claimed = _claim(requester, state["initiator_id"], str(claim_token))
    credential = claimed.get("credential")
    assert credential, f"first claim returned no credential: {claimed!r}"
    return str(credential)


@pytest.fixture
def receiver_assigned_clearance_path(
    live_kamiwaza_session_client: KamiwazaClient,
    live_kamiwaza_peer_client: KamiwazaClient,
    live_peer_base_url: str,
    live_base_url: str,
) -> Iterator[dict[str, Any]]:
    state: dict[str, Any] = {
        "initiator": live_kamiwaza_session_client,
        "receiver": live_kamiwaza_peer_client,
    }
    try:
        _provision_pair(state, live_peer_base_url)
        fixture_support.provision_gated_dataset(state)
        requester = _provision_requester(state, live_base_url)
        credential = _approve_and_claim(state, requester)
        claims = _decode_jwt_payload(credential)
        guest_sub = claims.get("sub")
        assert guest_sub, f"credential carries no guest subject: {claims!r}"
        state["guest_sub"] = str(guest_sub)
        assigned = _obj(
            state["receiver"],
            "GET",
            f"/cluster/federations/{state['receiver_id']}/guests/"
            f"{state['guest_sub']}/attributes",
        )
        yield {
            "assigned_attributes": assigned.get("attributes"),
            "credential": credential,
            "dataset_urn": state["dataset_urn"],
            "federation_name": state["name"],
            "requester": requester,
        }
    finally:
        fixture_support.cleanup(state)


def test_receiver_assigned_clearance_claim_reaches_dataset_gate_over_mesh(
    receiver_assigned_clearance_path: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = receiver_assigned_clearance_path
    assert path["assigned_attributes"] == {"clearance": "S"}

    credential = path["credential"]
    claims = _decode_jwt_payload(credential)
    assert claims.get("clearance") == "S", claims

    requester = path["requester"]
    local_token = requester.get_bearer_token()
    assert local_token, "requester's local access token is unavailable"
    env_name = "KAMIWAZA_FEDERATION_CREDENTIAL_" + path[
        "federation_name"
    ].upper().replace("-", "_")
    monkeypatch.setenv(env_name, credential)
    rows, gate_audits = mc.mesh_retrieve_through_gate(
        requester,
        requester.base_url,
        local_token,
        path["federation_name"],
        path["dataset_urn"],
        verify=False,
    )

    expected = [row for row in mc.records() if row["classification"] in {"U", "S"}]
    assert sorted(rows, key=lambda row: row["id"]) == expected
    mc.assert_persona_result("S", rows, gate_audits)
