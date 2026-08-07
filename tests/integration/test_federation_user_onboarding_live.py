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

Topology: the **receiver** (peer cluster) owns the per-federation realm, mints
guests, and serves the approve/deny queue. The **initiator** owns the requester
— ``request`` and ``claim`` are initiator operations, and ``claim`` makes a
PSK-signed mesh call OUT to the receiver to fetch the credential it minted.

Which side a call goes to is therefore a correctness property, not a style
choice. This file previously drove request/approve/claim entirely at the
receiver, which worked only while the claim was a process-local read; ENG-9730
made it a real cross-cluster leg (a local dict "returned ``200 {"credential":
null}`` on every real two-cluster deployment while passing single-process
tests"), so a receiver-side claim now dials a peer address the receiver's own
row does not hold.

The two clusters keep SEPARATE rows for one logical request, with separate ids
linked by ``external_id`` — the id handed to the requester is meaningless at the
receiver, which is what ``_receiver_request_id`` resolves.

Gated by ``requires_two_clusters`` + ``KAMIWAZA_PEER_BASE_URL`` /
``KAMIWAZA_PEER_API_KEY``, so it auto-deselects on PRs without a peer rig.

Fleet rig: spark-1 (receiver) <-> spark-2 (initiator/source). Serve on the
per-host FQDN (spark-N.kale.wemodulate.com) so istio Host-header routing
resolves.

Two transports are covered, and they are different properties:

``TestPerUserOnboarding`` drives request/approve/claim against the receiver
directly, isolating the *identity* properties from the wire. If the mesh leg
breaks, these still pin down what onboarding is supposed to produce.

``TestOnboardingOverTheMesh`` makes the request on the INITIATOR and never
touches the receiver to create it, so the only way the row can appear on the
receiver's queue is the PSK-signed forward (T1.3, ENG-9465). That is the leg the
product actually uses: a requester reaches their own cluster, not the peer's.
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


# The relation each approval grants. ``viewer`` on a ``dataset`` is the shape the
# approve schema documents by example and the one an operator reaches for first;
# it is also well clear of the F5 forbidden set (admin/publisher/owner), so a
# rejection here means the grant path is broken rather than the guard working.
GRANT_RELATION = "viewer"
GRANT_NAMESPACE = "dataset"


def _grant_subject_ids(
    client: KamiwazaClient, namespace: str, object_id: str
) -> set[str]:
    """Subject ids holding a grant on one object, read from the ReBAC store.

    Deliberately NOT ``GET /cluster/federations/{id}/users`` -> ``initial_tuples``:
    that field echoes what the approver ASKED for, recorded on the allowlist row
    before seeding runs and left in place when seeding fails. Asserting on it
    passes whether or not a single relation ever landed. This endpoint reads the
    relationship store itself, so it can tell the difference.
    """
    rows = _rows(
        client, f"/authz/resources/{namespace}/grants?object_id={object_id}"
    )
    return {str((row.get("subject") or {}).get("id") or "") for row in rows}


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


def _self_request_onboarding(
    client: KamiwazaClient, federation_id: str, justification: str
) -> dict[str, Any]:
    """Record a SELF-SERVICE onboarding request as whoever ``client`` is.

    ``external_id`` is deliberately omitted so the subject is the caller. That
    is the only shape that returns a claim token: ENG-9731 withholds it from a
    DELEGATED request, because handing an admin the token minted for someone
    else would let the admin claim that person's credential.

    This fixture used to drive both requesters from one admin session with an
    explicit ``external_id``, which is exactly the impersonation ENG-9731
    closed — so the shortcut stopped working and had to become two real
    sessions. The receiver's queue withholds the token too, so there is no
    admin-side way to recover it; a per-requester session is the honest fix,
    not a workaround.
    """
    return _obj(
        client,
        "POST",
        _onboarding_path(federation_id, "/request"),
        json={"justification": justification},
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


def _receiver_request_id(
    receiver_client: KamiwazaClient, receiver_fed_id: str, external_id: str
) -> str:
    """The receiver-side row matching ``external_id``, for the approve/deny leg.

    The initiator and receiver hold SEPARATE rows with separate ids for one
    logical request, so the id returned to the requester cannot be used against
    the receiver. Matching on ``external_id`` is what links them.
    """
    rows = _rows(receiver_client, _onboarding_path(receiver_fed_id))
    for row in rows:
        if row.get("external_id") == external_id:
            request_id = row.get("id")
            assert request_id, f"receiver row carries no id: {row!r}"
            return str(request_id)
    raise AssertionError(
        f"no receiver-side row for {external_id!r}; the mesh forward did not "
        f"arrive. queue={rows!r}"
    )


@pytest.fixture(scope="module")
def onboarded_pair(
    onboarding_federation: dict[str, str],
    initiator_client: KamiwazaClient,
    receiver_client: KamiwazaClient,
    live_base_url: str,
) -> dict[str, Any]:
    """Two requesters carried all the way through request -> approve -> claim.

    Built once and shared, because the distinctness assertions all read the same
    two outcomes and re-minting per test would be slow and would obscure which
    property failed. Attribute values differ per requester so a collapsed
    implementation cannot satisfy both.

    Each approval also carries a real ``relation``, on a per-person object, in
    the shape the shipped review dialog sends (``buildApprovalBody`` in
    ``OnboardingReviewDialog.js`` emits ``{relation, object}`` and no subject).
    Approving with ``relations: []`` — which this fixture used to do — exercises
    none of the grant path, which is how three independent silent failures in it
    survived a passing live suite.
    """
    # Two ids for one logical federation. Which side a call goes to is not a
    # style choice: `request` and `claim` are INITIATOR operations (claim makes
    # a PSK-signed mesh call OUT to the receiver), while approve/deny are the
    # receiver's. This fixture used to drive everything at the receiver, which
    # worked only while the claim was a local read — ENG-9730 made it a real
    # cross-cluster leg, so a receiver-side claim now dials a peer address the
    # receiver's own row does not have and dies on DNS.
    receiver_fed_id = onboarding_federation["receiver_id"]
    initiator_fed_id = onboarding_federation["initiator_id"]
    suffix = uuid.uuid4().hex[:8]
    people = [
        {
            # A REAL local user, not a synthetic label: each persona
            # authenticates and requests for themselves (see the loop below).
            "username": f"alice-{suffix}",
            "justification": "Conjunction review for the Q3 collision window.",
            "attributes": {"clearance": "high", "country": "US"},
            "grant_object_id": f"live-onboarding-{suffix}-conjunctions",
        },
        {
            "username": f"bob-{suffix}",
            "justification": "Sensor tasking follow-up.",
            "attributes": {"clearance": "low", "country": "UK"},
            "grant_object_id": f"live-onboarding-{suffix}-tasking",
        },
    ]

    from ._mini_clearance import authed_client

    for person in people:
        # Each requester drives their OWN session. ENG-9731 returns the claim
        # token only when the requester IS the subject, so one admin session
        # standing in for both people cannot obtain either token — and the
        # distinctness this fixture exists to prove is only meaningful when the
        # two requests genuinely come from two identities.
        username = person["username"]
        # The persona is an INITIATOR-local user: the product's claim is that a
        # requester reaches their OWN cluster, and the receiver never sees a
        # local account for them at all — only the guest it mints.
        initiator_client.subjects.upsert(username, attributes={}, password=username)
        person["client"] = authed_client(
            live_base_url, username, username, verify=False
        )

        status = _self_request_onboarding(
            person["client"], initiator_fed_id, person["justification"]
        )
        assert status.get("status") == "REQUESTED", f"unexpected status: {status!r}"
        request_id = status.get("id")
        claim_token = status.get("claim_token")
        assert request_id, f"request response must carry an id: {status!r}"
        assert claim_token, (
            "a self-service requester must receive their own claim token "
            f"(ENG-9731 withholds it only from delegated requests): {status!r}"
        )
        # The subject is now the caller, so take the id the API assigned rather
        # than the synthetic one this fixture used to inject.
        person["external_id"] = status.get("external_id") or username

        # Approve on the RECEIVER, against the RECEIVER's row id. The id the
        # requester was handed belongs to the initiator's copy and means
        # nothing here — the two rows are linked by external_id.
        receiver_request_id = _receiver_request_id(
            receiver_client, receiver_fed_id, person["external_id"]
        )
        approved = _obj(
            receiver_client,
            "POST",
            _onboarding_path(receiver_fed_id, f"/{receiver_request_id}/approve"),
            json={
                "attributes": person["attributes"],
                "relations": [
                    {
                        "relation": GRANT_RELATION,
                        "object": f"{GRANT_NAMESPACE}:{person['grant_object_id']}",
                    }
                ],
            },
        )
        assert approved.get("status") == "APPROVED", f"approve failed: {approved!r}"

        # Claim from the REQUESTER's session, not the admin's. Claiming as
        # admin would be the impersonation ENG-9731 exists to prevent, so a
        # test that did it could pass while the property was broken.
        claimed = _claim(person["client"], initiator_fed_id, claim_token)
        credential = claimed.get("credential")
        assert credential, f"first claim must return the credential: {claimed!r}"

        person["request_id"] = request_id
        person["receiver_request_id"] = receiver_request_id
        person["claim_token"] = claim_token
        person["credential"] = credential

    # ``federation_id`` stays the RECEIVER's: every downstream assertion reads
    # receiver-side state (minted guests, the allowlist, the ReBAC store), which
    # is where the per-user properties are observable.
    return {
        "federation_id": receiver_fed_id,
        "initiator_federation_id": initiator_fed_id,
        "people": people,
    }


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

    def test_the_approved_relation_reaches_the_rebac_store(
        self, onboarded_pair: dict[str, Any], receiver_client: KamiwazaClient
    ) -> None:
        """A relation assigned at approval must actually grant the guest.

        Distinct identities are only half of M1: the point of a per-person guest
        is that the receiver can authorize that person, and authorization is a
        relation in the ReBAC store. Onboarding that mints a subject and grants
        it nothing produces an identity that can prove who it is and do nothing
        — which reads as success everywhere except here.
        """
        for person in onboarded_pair["people"]:
            sub = _decode_jwt_payload(person["credential"]).get("sub")
            holders = _grant_subject_ids(
                receiver_client, GRANT_NAMESPACE, person["grant_object_id"]
            )
            assert sub in holders, (
                f"approval granted {GRANT_RELATION} on "
                f"{GRANT_NAMESPACE}:{person['grant_object_id']} to "
                f"{person['external_id']}, but the store holds {holders or 'no'} "
                f"grants for it — guest {sub} got an identity with no access"
            )

    def test_one_guests_grant_does_not_reach_the_other(
        self, onboarded_pair: dict[str, Any], receiver_client: KamiwazaClient
    ) -> None:
        """Per-person grants must not leak across guests on one federation.

        The pre-M1 shared guest made every grant federation-wide by construction.
        A seeder that rendered the subject placeholder against the wrong user, or
        seeded once per federation rather than once per approval, would still
        satisfy the assertion above while re-collapsing authority here.
        """
        alice, bob = onboarded_pair["people"]
        bob_sub = _decode_jwt_payload(bob["credential"]).get("sub")
        holders = _grant_subject_ids(
            receiver_client, GRANT_NAMESPACE, alice["grant_object_id"]
        )
        assert bob_sub not in holders, (
            f"{bob['external_id']} holds a grant on "
            f"{alice['external_id']}'s object — per-person authority collapsed"
        )

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

    def test_claim_token_is_single_use(self, onboarded_pair: dict[str, Any]) -> None:
        """The token is spent on first success, so a leaked token cannot re-fetch
        a durable credential.

        Replayed on the INITIATOR by the token's own owner, because that is
        where single-use is enforced: ``claim_onboarding`` spends the token
        before making its mesh call, and the receiver's queue deliberately holds
        no token at all. Replaying at the receiver would prove nothing — there
        is nothing there to spend.
        """
        person = onboarded_pair["people"][0]
        replay = _claim(
            person["client"],
            onboarded_pair["initiator_federation_id"],
            person["claim_token"],
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
        self,
        onboarding_federation: dict[str, str],
        initiator_client: KamiwazaClient,
        receiver_client: KamiwazaClient,
        live_base_url: str,
    ) -> None:
        """A denial has to say why — the requester's UI shows the reason, and a
        silent DENIED is indistinguishable from a stuck request.

        Carol requests for herself so she holds her own claim token: a delegated
        request returns none (ENG-9731), and this test's last assertion needs a
        real token to prove a DENIED request yields no credential. Passing the
        `None` from a delegated response made the claim 404 on a token that had
        never existed — which looks like the denial working, and is not.
        """
        from ._mini_clearance import authed_client

        receiver_fed_id = onboarding_federation["receiver_id"]
        initiator_fed_id = onboarding_federation["initiator_id"]
        username = f"carol-{uuid.uuid4().hex[:8]}"
        initiator_client.subjects.upsert(username, attributes={}, password=username)
        carol = authed_client(live_base_url, username, username, verify=False)

        status = _self_request_onboarding(
            carol, initiator_fed_id, "Ad-hoc access for a one-off review."
        )
        claim_token = status.get("claim_token")
        assert claim_token, f"carol must hold her own claim token: {status!r}"
        external_id = status.get("external_id") or username

        reason = "clearance not verified"
        receiver_request_id = _receiver_request_id(
            receiver_client, receiver_fed_id, external_id
        )
        denied = _obj(
            receiver_client,
            "POST",
            _onboarding_path(receiver_fed_id, f"/{receiver_request_id}/deny"),
            json={"reason": reason},
        )
        assert denied.get("status") == "DENIED", f"deny failed: {denied!r}"
        assert denied.get("denied_reason") == reason

        claimed = _claim(carol, initiator_fed_id, claim_token)
        assert not claimed.get("credential"), (
            f"a denied request yielded a credential: {claimed!r}"
        )


@pytest.fixture(scope="module")
def mesh_request(
    onboarding_federation: dict[str, str], initiator_client: KamiwazaClient
) -> dict[str, Any]:
    """One self-service request made on the INITIATOR, forwarded over the mesh.

    Deliberately omits ``external_id``: the mesh leg's whole claim is that the
    initiator vouches for its OWN caller, so letting the test name the identity
    would skip the part under test. The caller's identity comes back on the
    response, which is what the receiver-side assertions match against.
    """
    fed_id = onboarding_federation["initiator_id"]
    status = _obj(
        initiator_client,
        "POST",
        _onboarding_path(fed_id, "/request"),
        json={"justification": "Cross-cluster conjunction review (mesh leg)."},
    )
    assert status.get("status") == "REQUESTED", f"unexpected status: {status!r}"
    external_id = status.get("external_id")
    assert external_id, (
        f"the initiator must stamp the caller's identity on the row: {status!r}"
    )
    return {
        "initiator_id": fed_id,
        "receiver_id": onboarding_federation["receiver_id"],
        "external_id": external_id,
        "status": status,
    }


class TestOnboardingOverTheMesh:
    """ENG-9465 / M1 T1.3 — the request travels initiator -> receiver."""

    def test_the_request_arrives_on_the_receiver_queue(
        self, mesh_request: dict[str, Any], receiver_client: KamiwazaClient
    ) -> None:
        """The load-bearing assertion for the mesh leg.

        Nothing in this module ever posts this identity to the receiver. If it
        is on the receiver's queue, the signed forward carried it there.
        """
        rows = _rows(
            receiver_client, _onboarding_path(mesh_request["receiver_id"])
        )
        arrived = [
            r for r in rows if r.get("external_id") == mesh_request["external_id"]
        ]
        assert arrived, (
            f"{mesh_request['external_id']} never reached the receiver queue; "
            f"saw {[r.get('external_id') for r in rows]!r}"
        )

    def test_the_receiver_copy_carries_no_claim_token(
        self, mesh_request: dict[str, Any], receiver_client: KamiwazaClient
    ) -> None:
        """The requester's bearer stays on the side that minted it.

        The initiator already handed the token to its caller; a second copy in
        the receiver's queue would let a receiver operator claim the credential
        on that user's behalf.
        """
        rows = _rows(
            receiver_client, _onboarding_path(mesh_request["receiver_id"])
        )
        arrived = next(
            r for r in rows if r.get("external_id") == mesh_request["external_id"]
        )
        assert not arrived.get("claim_token"), (
            f"receiver queue leaked a claim token: {arrived!r}"
        )

    def test_the_requester_polls_their_own_cluster(
        self, mesh_request: dict[str, Any], initiator_client: KamiwazaClient
    ) -> None:
        """``/onboarding/me`` answers on the initiator, with no peer credentials.

        This is the surface a non-admin actually has: they can reach their own
        cluster and nothing else.
        """
        mine = _obj(
            initiator_client, "GET", _onboarding_path(mesh_request["initiator_id"], "/me")
        )
        assert mine.get("external_id") == mesh_request["external_id"], (
            f"/me returned somebody else's request: {mine!r}"
        )

    def test_the_receivers_decision_reaches_the_requester(
        self,
        mesh_request: dict[str, Any],
        initiator_client: KamiwazaClient,
        receiver_client: KamiwazaClient,
    ) -> None:
        """Approve on the receiver; the initiator's ``/me`` reflects it.

        The return leg is a pull, not a push: receiver -> initiator is the
        direction that cannot be relied on (the receiver may have no route back),
        so ``/me`` refreshes from the receiver when asked.
        """
        rows = _rows(
            receiver_client, _onboarding_path(mesh_request["receiver_id"])
        )
        arrived = next(
            r for r in rows if r.get("external_id") == mesh_request["external_id"]
        )
        approved = _obj(
            receiver_client,
            "POST",
            _onboarding_path(
                mesh_request["receiver_id"], f"/{arrived['id']}/approve"
            ),
            json={"attributes": {"clearance": "high"}, "relations": []},
        )
        assert approved.get("status") == "APPROVED", f"approve failed: {approved!r}"

        mine = _obj(
            initiator_client, "GET", _onboarding_path(mesh_request["initiator_id"], "/me")
        )
        assert mine.get("status") == "APPROVED", (
            f"the initiator never learned of the approval: {mine!r}"
        )
