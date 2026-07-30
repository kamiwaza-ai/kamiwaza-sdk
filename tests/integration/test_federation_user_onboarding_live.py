"""ENG-9394 — Live per-user federated onboarding (M1) walkthrough.

The live counterpart to the mocked backend unit tests. It proves the property
M1 exists for: **no identity collapse**. Before this milestone a receiver_realm
federation minted ONE guest per federation and the initiator stored ONE
credential keyed by federation name, so every initiator user arrived on the
receiver as the same ``guest_sub``. Attributes attached to that shared subject
applied to everybody.

Walkthrough::

    pair(receiver_realm) -> two distinct requesters each request onboarding ->
    receiver approves each with its OWN attributes -> each claim yields a
    DISTINCT credential -> the allowlist records linked_external_user per guest
    -> a claim token is spent on first use -> deny records a reason

The assertions that carry the milestone are the *distinctness* ones: two
requesters must produce two guest subjects and two credentials. A test that only
checked "a credential came back" would have passed on the pre-M1 code.

Topology mirrors test_federation_receiver_realm_live.py: the **receiver** (peer
cluster) owns the per-federation realm, mints guests, and serves the onboarding
queue, so onboarding calls run against ``receiver_client``. The initiator only
drives the pairing handshake that provisions the receiver's realm.

Gated by ``requires_two_clusters`` + ``KAMIWAZA_PEER_BASE_URL`` /
``KAMIWAZA_PEER_API_KEY``, so it auto-deselects on PRs without a peer rig.

Fleet rig: spark-1 (receiver) <-> spark-2 (initiator/source). Serve on the
per-host FQDN (spark-N.kale.wemodulate.com) so istio Host-header routing
resolves.

Scope note: the request leg is recorded on the receiver directly. Carrying the
request over the mesh from the initiator is deliberately deferred (design §6.3
M1 -> M2), so this test drives request/approve/claim against the receiver and
asserts the identity properties, not the transport.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from typing import Any, Iterator

import pytest

from kamiwaza_sdk import KamiwazaClient

logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
    pytest.mark.requires_two_clusters,
]


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Base64url-decode a JWT payload WITHOUT verifying the signature.

    Inspection only: the subject claim is what distinguishes one minted guest
    from another, and that is the property under test.
    """
    parts = token.split(".")
    assert len(parts) >= 2, f"credential is not a JWT (got {len(parts)} segs)"
    payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


def _onboarding_path(federation_id: str, suffix: str = "") -> str:
    return f"/cluster/federations/{federation_id}/onboarding{suffix}"


def _obj(client: KamiwazaClient, method: str, path: str, **kwargs) -> dict[str, Any]:
    """One request that must answer with an object.

    ``KamiwazaClient._request`` is typed Optional, and a None here means the
    endpoint answered with an empty body — a real failure worth naming at the
    call site rather than an AttributeError three lines later.
    """
    body = client._request(method, path, **kwargs)
    assert isinstance(body, dict), f"{method} {path} returned {body!r}, expected object"
    return body


def _rows(client: KamiwazaClient, path: str) -> list[dict[str, Any]]:
    """A collection endpoint, tolerating both the bare-list and {items: []} shapes."""
    body = client._request("GET", path)
    rows = body if isinstance(body, list) else (body or {}).get("items", [])
    assert isinstance(rows, list), f"GET {path} returned {body!r}, expected a list"
    return rows


def _request_onboarding(
    client: KamiwazaClient, federation_id: str, external_id: str, justification: str
) -> dict[str, Any]:
    """Record one onboarding request and return its status (with claim token).

    ``external_id`` is set explicitly because the test drives both requesters
    from one admin session; the self-service path omits it and uses the caller.
    """
    return _obj(
        client,
        "POST",
        _onboarding_path(federation_id, "/request"),
        json={
            "justification": justification,
            "external_id": external_id,
            "email": f"{external_id.split('@')[0]}@example.test",
        },
    )


def _claim(client: KamiwazaClient, federation_id: str, token: str) -> dict[str, Any]:
    return _obj(
        client,
        "POST",
        _onboarding_path(federation_id, "/claim"),
        json={"claim_token": token},
    )


@pytest.fixture(scope="module")
def initiator_client(live_kamiwaza_session_client: KamiwazaClient) -> KamiwazaClient:
    return live_kamiwaza_session_client


@pytest.fixture(scope="module")
def receiver_client(live_kamiwaza_peer_client: KamiwazaClient) -> KamiwazaClient:
    return live_kamiwaza_peer_client


@pytest.fixture(scope="module")
def onboarding_federation(
    initiator_client: KamiwazaClient,
    receiver_client: KamiwazaClient,
    live_peer_base_url: str,
) -> Iterator[dict[str, str]]:
    """A receiver_realm federation for the duration of the module.

    Same handshake as the receiver_realm walkthrough: the receiver pairs in
    receiver_realm mode and the initiator drives the handshake, which fires the
    receiver's realm-provisioning hook. Disconnect tears the realm back down.
    """
    name = f"eng9394-onb-{uuid.uuid4().hex[:8]}"
    pair_psk = str(uuid.uuid4())

    receiver_fed = receiver_client.federations.pair(
        name=name,
        role="receiver",
        preshared_key=pair_psk,
        realm_scope="per_federation",
    )
    receiver_fed_id = str(receiver_fed.id)
    try:
        # realm_scope is set on BOTH sides. It reads like a receiver-only
        # concern (only the receiver provisions a realm), but the create-time
        # identity stamp is resolved per row: without it the initiator's row
        # falls through to the legacy source-trusted peer_kc mode, which is
        # refused unless ALLOW_UNTRUSTED_FEDERATION is on
        # (cluster/federation.py::_resolve_new_federation_identity_stamp).
        initiator_fed = initiator_client.federations.pair(
            name=name,
            role="initiator",
            remote_url=live_peer_base_url,
            preshared_key=pair_psk,
            realm_scope="per_federation",
        )
    except Exception:
        try:
            receiver_client._request(
                "POST", f"/cluster/federations/{receiver_fed_id}/disconnect"
            )
        except Exception as cleanup_exc:  # pragma: no cover - best effort
            logger.warning(
                "failed to clean up orphaned receiver federation %s: %s",
                receiver_fed_id,
                cleanup_exc,
            )
        raise

    state = {
        "initiator_id": str(initiator_fed.id),
        "receiver_id": receiver_fed_id,
        "name": name,
    }
    try:
        yield state
    finally:
        for label, client, fed_id in (
            ("initiator", initiator_client, state["initiator_id"]),
            ("receiver", receiver_client, state["receiver_id"]),
        ):
            try:
                client._request("POST", f"/cluster/federations/{fed_id}/disconnect")
            except Exception as exc:  # pragma: no cover - teardown best-effort
                logger.warning("failed to disconnect %s %s: %s", label, fed_id, exc)


@pytest.fixture(scope="module")
def onboarded_pair(
    onboarding_federation: dict[str, str], receiver_client: KamiwazaClient
) -> dict[str, Any]:
    """Two requesters carried all the way through request -> approve -> claim.

    Built once and shared, because the distinctness assertions all read the same
    two outcomes and re-minting per test would be slow and would obscure which
    property failed. Attribute values differ per requester so a collapsed
    implementation cannot satisfy both.
    """
    fed_id = onboarding_federation["receiver_id"]
    suffix = uuid.uuid4().hex[:8]
    people = [
        {
            "external_id": f"alice-{suffix}@src",
            "justification": "Conjunction review for the Q3 collision window.",
            "attributes": {"clearance": "high", "country": "US"},
        },
        {
            "external_id": f"bob-{suffix}@src",
            "justification": "Sensor tasking follow-up.",
            "attributes": {"clearance": "low", "country": "UK"},
        },
    ]

    for person in people:
        status = _request_onboarding(
            receiver_client, fed_id, person["external_id"], person["justification"]
        )
        assert status.get("status") == "REQUESTED", f"unexpected status: {status!r}"
        request_id = status.get("id")
        claim_token = status.get("claim_token")
        assert request_id, f"request response must carry an id: {status!r}"
        assert claim_token, f"requester must receive a claim token: {status!r}"

        approved = _obj(
            receiver_client,
            "POST",
            _onboarding_path(fed_id, f"/{request_id}/approve"),
            json={"attributes": person["attributes"], "relations": []},
        )
        assert approved.get("status") == "APPROVED", f"approve failed: {approved!r}"

        claimed = _claim(receiver_client, fed_id, claim_token)
        credential = claimed.get("credential")
        assert credential, f"first claim must return the credential: {claimed!r}"

        person["request_id"] = request_id
        person["claim_token"] = claim_token
        person["credential"] = credential

    return {"federation_id": fed_id, "people": people}


class TestPerUserOnboarding:
    """Live per-user onboarding round-trip against a real peer cluster."""

    def test_each_requester_gets_a_distinct_guest_subject(
        self, onboarded_pair: dict[str, Any]
    ) -> None:
        """THE M1 property: two requesters, two guest subjects.

        Pre-M1 both credentials carried the same ``sub`` because one guest
        served the whole federation. Distinct subjects are what make
        receiver-side attributes and audit per-person.
        """
        subs = [
            _decode_jwt_payload(p["credential"]).get("sub")
            for p in onboarded_pair["people"]
        ]
        assert all(subs), f"every credential must carry a subject: {subs!r}"
        assert len(set(subs)) == len(subs), (
            f"identity collapse: requesters share a guest subject {subs!r}"
        )

    def test_credentials_are_not_shared(self, onboarded_pair: dict[str, Any]) -> None:
        """Distinct subjects would still collapse if the same bearer were handed
        to both, so assert the credential strings differ too."""
        creds = [p["credential"] for p in onboarded_pair["people"]]
        assert len(set(creds)) == len(creds), "requesters were handed the same bearer"

    def test_allowlist_records_the_initiator_user_behind_each_guest(
        self, onboarded_pair: dict[str, Any], receiver_client: KamiwazaClient
    ) -> None:
        """``linked_external_user`` is what lets an operator answer 'who is this
        guest?' — without it the receiver has an opaque UUID per person and the
        audit trail stops at the federation."""
        rows = _rows(
            receiver_client,
            f"/cluster/federations/{onboarded_pair['federation_id']}/users",
        )
        linked = {
            r.get("linked_external_user") for r in rows if r.get("linked_external_user")
        }
        for person in onboarded_pair["people"]:
            assert person["external_id"] in linked, (
                f"{person['external_id']} not linked to a guest; allowlist={rows!r}"
            )

    def test_claim_token_is_single_use(
        self, onboarded_pair: dict[str, Any], receiver_client: KamiwazaClient
    ) -> None:
        """The token is spent on first success, so a leaked token cannot re-fetch
        a durable credential."""
        person = onboarded_pair["people"][0]
        replay = _claim(
            receiver_client, onboarded_pair["federation_id"], person["claim_token"]
        )
        assert not replay.get("credential"), (
            f"a spent claim token returned the credential again: {replay!r}"
        )

    def test_queue_lists_requests_without_leaking_claim_tokens(
        self, onboarded_pair: dict[str, Any], receiver_client: KamiwazaClient
    ) -> None:
        """The operator reviewing the queue must not be able to claim on a
        requester's behalf, so the listing omits the token."""
        rows = _rows(receiver_client, _onboarding_path(onboarded_pair["federation_id"]))
        listed = {r.get("external_id") for r in rows}
        for person in onboarded_pair["people"]:
            assert person["external_id"] in listed, f"queue is missing {person!r}"
        assert not any(r.get("claim_token") for r in rows), (
            "the receiver's queue leaked a claim token"
        )

    def test_denied_request_records_its_reason(
        self, onboarding_federation: dict[str, str], receiver_client: KamiwazaClient
    ) -> None:
        """A denial has to say why — the requester's UI shows the reason, and a
        silent DENIED is indistinguishable from a stuck request."""
        fed_id = onboarding_federation["receiver_id"]
        external_id = f"carol-{uuid.uuid4().hex[:8]}@src"
        status = _request_onboarding(
            receiver_client, fed_id, external_id, "Ad-hoc access for a one-off review."
        )
        reason = "clearance not verified"
        denied = _obj(
            receiver_client,
            "POST",
            _onboarding_path(fed_id, f"/{status['id']}/deny"),
            json={"reason": reason},
        )
        assert denied.get("status") == "DENIED", f"deny failed: {denied!r}"
        assert denied.get("denied_reason") == reason

        claimed = _claim(receiver_client, fed_id, status["claim_token"])
        assert not claimed.get("credential"), (
            f"a denied request yielded a credential: {claimed!r}"
        )
