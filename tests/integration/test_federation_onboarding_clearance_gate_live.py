"""ENG-10096 — receiver-assigned onboarding clearance reaches a mesh gate.

The receiver approves self-service guests with receiver-owned attributes and a
viewer grant on a MiniClearanceGate dataset. Canonical ``tenant_id=__default__``
plus ``clearance=S`` must survive the receiver's offline-token exchange and
return the known-answer U/S rows. Missing, blank, legacy-only,
whitespace-wrapped, and nondefault access-token claims remain fail-closed even
when the caller spoofs a default-tenant header. A separate guest reuses one
opaque offline credential after Core's 60-second claims cache boundary, then
proves revocation is still enforced while the refreshed claims are cached.

``MINI_CLEARANCE_DATASET_PATH`` is the receiver-visible CSV written from
``mini_clearance_records.json`` by ``tests.integration._gate_fixture``. The test
creates its own dataset catalog row and gate binding; it never owns or removes
that shared provisioner fixture.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import pytest

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.services.federation_credentials import (
    FEDERATION_CREDENTIAL_HEADER,
    federation_credential_headers,
)

from . import _mini_clearance as mc
from . import _onboarding_clearance_fixture as fixture_support
from .test_federation_user_onboarding_live import (
    _claim,
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
    pytest.mark.requires_receiver_realm,
]

_DEFAULT_TENANT_ID = "__default__"
_RECEIVER_REALM_CLAIMS_CACHE_TTL_SECONDS = 60.0
_RECEIVER_REFRESH_BOUNDARY_WAIT_SECONDS = _RECEIVER_REALM_CLAIMS_CACHE_TTL_SECONDS + 2.0


@dataclass(frozen=True)
class _TenantRejectionCase:
    case_id: str
    approval_attributes: dict[str, str]
    expected_assigned_attributes: dict[str, str]
    expected_status: int
    expected_reason: str


_TENANT_REJECTION_CASES = (
    _TenantRejectionCase(
        "missing-canonical",
        {"clearance": "S"},
        {"clearance": "S"},
        401,
        "tenant_required",
    ),
    _TenantRejectionCase(
        "legacy-only",
        {"clearance": "S", "tenant": _DEFAULT_TENANT_ID},
        {"clearance": "S", "tenant": _DEFAULT_TENANT_ID},
        401,
        "tenant_required",
    ),
    _TenantRejectionCase(
        "canonical-blank",
        {"clearance": "S", "tenant_id": ""},
        {"clearance": "S"},
        401,
        "tenant_required",
    ),
    _TenantRejectionCase(
        "canonical-whitespace-wrapped",
        {"clearance": "S", "tenant_id": " __default__ "},
        {"clearance": "S", "tenant_id": " __default__ "},
        403,
        "mesh_tenant_not_admitted",
    ),
    _TenantRejectionCase(
        "canonical-nondefault",
        {"clearance": "S", "tenant_id": "tenant-a"},
        {"clearance": "S", "tenant_id": "tenant-a"},
        403,
        "mesh_tenant_not_admitted",
    ),
)


def _receiver_approval_body(
    dataset_urn: str,
    *,
    attributes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build the receiver-owned approval used by the data-plane proofs."""
    assigned = (
        {"clearance": "S", "tenant_id": _DEFAULT_TENANT_ID}
        if attributes is None
        else dict(attributes)
    )
    return {
        "attributes": assigned,
        "relations": [
            {
                "relation": "viewer",
                "object": f"dataset:{dataset_urn}",
            }
        ],
    }


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


def _provision_requester(
    state: dict[str, Any],
    live_base_url: str,
    *,
    label: str,
) -> KamiwazaClient:
    username = f"eng10096-{label}-{uuid.uuid4().hex[:10]}"
    state["initiator"].subjects.upsert(username, attributes={}, password=username)
    state.setdefault("requester_usernames", []).append(username)
    requester = mc.authed_client(live_base_url, username, username, verify=False)
    state.setdefault("requester_clients", []).append(requester)
    return requester


def _required_onboarding_claim_material(status: Mapping[str, Any]) -> tuple[str, str]:
    if status.get("status") != "REQUESTED":
        raise AssertionError(
            "self-service onboarding request did not enter REQUESTED state"
        )
    claim_token = status.get("claim_token")
    if not claim_token:
        raise AssertionError("self-service onboarding request returned no claim token")
    external_id = status.get("external_id")
    if not external_id:
        raise AssertionError("self-service onboarding request returned no external id")
    return str(claim_token), str(external_id)


def _require_approved_status(status: Mapping[str, Any], message: str) -> None:
    if status.get("status") != "APPROVED":
        raise AssertionError(message)


def _claimed_guest_subs(body: Any, external_id: str) -> list[str]:
    if isinstance(body, list):
        rows = body
    elif isinstance(body, dict):
        rows = body.get("items", [])
    else:
        rows = []
    matches = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("linked_external_user") != external_id:
            continue
        guest_sub = row.get("external_id")
        if guest_sub:
            matches.append(str(guest_sub))
    return matches


def _required_claim_identity(
    state: Mapping[str, Any],
    claimed: Mapping[str, Any],
    external_id: str,
) -> tuple[str, str]:
    """Resolve identity from receiver-owned state; keep the refresh token opaque."""
    credential = claimed.get("credential")
    if not credential:
        raise AssertionError("receiver claim returned no federation credential")
    body = state["receiver"]._request(
        "GET", f"/cluster/federations/{state['receiver_id']}/users"
    )
    matches = _claimed_guest_subs(body, external_id)
    if len(matches) != 1:
        raise AssertionError("receiver roster did not expose exactly one claimed guest")
    return str(credential), matches[0]


def _approve_and_claim(
    state: dict[str, Any],
    requester: KamiwazaClient,
    *,
    attributes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    status = _self_request_onboarding(
        requester,
        state["initiator_id"],
        "ENG-10096 receiver-assigned clearance gate proof.",
    )
    claim_token, external_id = _required_onboarding_claim_material(status)
    state.setdefault("onboarding_external_ids", []).append(external_id)

    request_id = _receiver_request_id(
        state["receiver"], state["receiver_id"], external_id
    )
    approved = _obj(
        state["receiver"],
        "POST",
        _onboarding_path(state["receiver_id"], f"/{request_id}/approve"),
        json=_receiver_approval_body(
            state["dataset_urn"],
            attributes=attributes,
        ),
    )
    _require_approved_status(
        approved,
        "receiver onboarding approval did not enter APPROVED state",
    )
    mine = _obj(
        requester,
        "GET",
        _onboarding_path(state["initiator_id"], "/me"),
    )
    _require_approved_status(mine, "receiver onboarding approval did not propagate")
    claimed = _claim(requester, state["initiator_id"], claim_token)
    credential, guest_sub = _required_claim_identity(state, claimed, external_id)
    state.setdefault("dataset_guest_subs", []).append(guest_sub)
    return {
        "credential": credential,
        "external_id": external_id,
        "guest_sub": guest_sub,
    }


def _provision_clearance_guest(
    state: dict[str, Any],
    *,
    label: str,
    attributes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    requester = _provision_requester(
        state,
        state["live_base_url"],
        label=label,
    )
    claimed = _approve_and_claim(state, requester, attributes=attributes)
    assigned = _obj(
        state["receiver"],
        "GET",
        f"/cluster/federations/{state['receiver_id']}/guests/"
        f"{claimed['guest_sub']}/attributes",
    )
    return {
        **claimed,
        "assigned_attributes": assigned.get("attributes"),
        "dataset_urn": state["dataset_urn"],
        "federation_name": state["name"],
        "receiver": state["receiver"],
        "receiver_id": state["receiver_id"],
        "requester": requester,
    }


def _install_federation_credential(
    path: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = path["credential"]
    env_name = "KAMIWAZA_FEDERATION_CREDENTIAL_" + path[
        "federation_name"
    ].upper().replace("-", "_")
    monkeypatch.setenv(env_name, credential)
    expected = {FEDERATION_CREDENTIAL_HEADER: credential}
    if federation_credential_headers(path["federation_name"]) != expected:
        raise AssertionError("federation credential installation could not be verified")


def _assert_clearance_retrieval(path: dict[str, Any]) -> None:
    requester = path["requester"]
    local_token = requester.get_bearer_token()
    assert local_token, "requester's local access token is unavailable"
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


def _mesh_job_create_response(
    path: dict[str, Any],
    credential: str,
) -> tuple[int, Any]:
    """Call the retrieval data plane with an explicitly spoofed tenant header."""
    requester = path["requester"]
    local_token = requester.get_bearer_token()
    assert local_token, "requester's local access token is unavailable"
    selector = quote(path["federation_name"], safe="")
    url = f"{requester.base_url.rstrip('/')}/mesh/{selector}/api/retrieval/jobs"
    headers = {
        "Authorization": f"Bearer {local_token}",
        FEDERATION_CREDENTIAL_HEADER: credential,
        "X-Tenant-Id": _DEFAULT_TENANT_ID,
    }
    with requester.session.post(
        url,
        json={"dataset_urn": path["dataset_urn"]},
        headers=headers,
        verify=False,
        timeout=120,
    ) as response:
        status = response.status_code
        try:
            payload = response.json()
        except ValueError:
            pytest.fail("receiver tenant probe returned a non-JSON body", pytrace=False)
    return status, payload


def _denial_reason(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    detail = payload.get("detail")
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        reason = detail.get("reason")
        return str(reason) if reason is not None else None
    return None


def _assert_mesh_denial(
    status: int,
    payload: Any,
    *,
    expected_status: int,
    expected_reason: str,
) -> None:
    assert (
        status == expected_status
    ), f"expected receiver denial status {expected_status}, got {status}"
    assert (
        _denial_reason(payload) == expected_reason
    ), f"expected receiver denial reason {expected_reason!r}"


def _exercise_receiver_refresh_boundary(
    path: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    *,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    credential = path["credential"]
    _install_federation_credential(path, monkeypatch)

    _assert_clearance_retrieval(path)
    sleeper(_RECEIVER_REFRESH_BOUNDARY_WAIT_SECONDS)
    _assert_clearance_retrieval(path)

    federation = path["receiver"].federations.by_id(path["receiver_id"])
    result = federation.guests.revoke(path["guest_sub"])
    assert result.get("success") is True
    status, payload = _mesh_job_create_response(path, credential)
    _assert_mesh_denial(
        status,
        payload,
        expected_status=403,
        expected_reason="revoked_guest",
    )


@pytest.fixture(scope="module")
def receiver_realm_clearance_edge(
    live_kamiwaza_session_client: KamiwazaClient,
    live_kamiwaza_peer_client: KamiwazaClient,
    live_peer_base_url: str,
    live_base_url: str,
) -> Iterator[dict[str, Any]]:
    state: dict[str, Any] = {
        "initiator": live_kamiwaza_session_client,
        "receiver": live_kamiwaza_peer_client,
        "live_base_url": live_base_url,
    }
    with fixture_support.cleanup_preserving_primary(state):
        _provision_pair(state, live_peer_base_url)
        fixture_support.provision_gated_dataset(state)
        yield state


def test_receiver_assigned_default_tenant_reaches_dataset_gate_over_mesh(
    receiver_realm_clearance_edge: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _provision_clearance_guest(
        receiver_realm_clearance_edge,
        label="canonical-default",
    )
    assert path["assigned_attributes"] == {
        "clearance": "S",
        "tenant_id": _DEFAULT_TENANT_ID,
    }

    _install_federation_credential(path, monkeypatch)
    # The durable credential is a refresh token and is intentionally opaque.
    # Exact U/S rows prove the receiver exchanged it, validated the resulting
    # access token, and supplied both tenant_id and clearance to the gate.
    _assert_clearance_retrieval(path)


@pytest.mark.parametrize(
    "case",
    [pytest.param(case, id=case.case_id) for case in _TENANT_REJECTION_CASES],
)
def test_receiver_tenant_claims_fail_closed_despite_default_caller_header(
    case: _TenantRejectionCase,
    receiver_realm_clearance_edge: dict[str, Any],
) -> None:
    path = _provision_clearance_guest(
        receiver_realm_clearance_edge,
        label=case.case_id,
        attributes=case.approval_attributes,
    )
    assert path["assigned_attributes"] == case.expected_assigned_attributes

    status, payload = _mesh_job_create_response(path, path["credential"])
    _assert_mesh_denial(
        status,
        payload,
        expected_status=case.expected_status,
        expected_reason=case.expected_reason,
    )


def test_receiver_offline_credential_refreshes_past_claims_cache_then_revokes(
    receiver_realm_clearance_edge: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _provision_clearance_guest(
        receiver_realm_clearance_edge,
        label="refresh-boundary",
    )

    _exercise_receiver_refresh_boundary(path, monkeypatch)
