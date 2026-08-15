"""Live request/approve pairing lifecycle (ENG-8977) and its refusals.

The onboarding walkthrough pairs by calling ``federations.pair`` on both sides,
which is the operator-drives-both-clusters shape. This file covers the other
one: the initiator *requests*, and an operator on the receiver reviews and
approves. That path owns state transitions the pair path never performs, and
until now nothing exercised them against a cluster.

The assertion that carries this file is the double-approve refusal. Approving a
federation that is not ``REQUESTED`` used to be **destructive**, not merely
redundant: ``_attach_psk_secret`` creates the catalog secret with
``clobber=False``, so a second approve failed on the already-existing secret and
took its create-path rollback, which DELETES the federation row. A
double-clicked Approve button orphaned the provisioned realm and every guest in
it while the peer still believed the federation was live.

Topology matches the onboarding suite: the **initiator** is the primary
``--live-base-url`` cluster (spark-2) and the **receiver** is
``--live-peer-base-url`` (spark-1). Serve on the per-host FQDN so istio's
Host-header routing resolves.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Iterator
from urllib.parse import urlparse

import pytest

from kamiwaza_sdk import KamiwazaClient

logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
    pytest.mark.requires_two_clusters,
]

STATUS_REQUESTED = "REQUESTED"
STATUS_APPROVED = "APPROVED"


def _obj(client: KamiwazaClient, method: str, path: str, **kwargs) -> dict[str, Any]:
    body = client._request(method, path, **kwargs)
    assert isinstance(body, dict), f"{method} {path} returned {body!r}, expected object"
    return body


def _federations(client: KamiwazaClient) -> list[dict[str, Any]]:
    body = client._request("GET", "/cluster/federations")
    rows = body if isinstance(body, list) else (body or {}).get("items", [])
    assert isinstance(rows, list), f"federation listing returned {body!r}"
    return rows


def _find_by_id(client: KamiwazaClient, federation_id: str) -> dict[str, Any] | None:
    for row in _federations(client):
        if str(row.get("id")) == str(federation_id):
            return row
    return None


def _federation_ids(client: KamiwazaClient) -> set[str]:
    return {str(row.get("id")) for row in _federations(client)}


def _host_of(url: str) -> str:
    host = urlparse(url).hostname
    assert host, f"could not parse a hostname out of {url!r}"
    return host


@pytest.fixture(scope="module")
def initiator_client(live_kamiwaza_session_client: KamiwazaClient) -> KamiwazaClient:
    return live_kamiwaza_session_client


@pytest.fixture(scope="module")
def receiver_client(live_kamiwaza_peer_client: KamiwazaClient) -> KamiwazaClient:
    return live_kamiwaza_peer_client


@pytest.fixture(scope="module")
def approved_federation(
    initiator_client: KamiwazaClient,
    receiver_client: KamiwazaClient,
    live_base_url: str,
    live_peer_base_url: str,
) -> Iterator[dict[str, Any]]:
    """One federation carried request -> approve, with both rows tracked.

    Module-scoped because approving is the expensive step (the receiver
    provisions a realm) and every assertion here reads the same outcome.
    """
    name = f"live-req-approve-{uuid.uuid4().hex[:8]}"
    psk = str(uuid.uuid4())

    # Correlate by which row APPEARS, not by name: the receiver labels the row
    # with the requester's advertised cluster name (``requester_cluster_name``
    # from the intake), not the initiator operator's label for the remote, so
    # every request from one cluster lands under the same display name.
    # ``request_token``, the real correlator, is not exposed on the listing.
    before = _federation_ids(receiver_client)

    requested = _obj(
        initiator_client,
        "POST",
        "/cluster/federations/request",
        json={
            "remote_cluster_name": name,
            "remote_ips": [{"ip": _host_of(live_peer_base_url), "primary": True}],
            "callback_hostname": _host_of(live_base_url),
            "preshared_key": psk,
        },
    )
    initiator_id = requested.get("id")
    assert initiator_id, f"request must return the initiator row: {requested!r}"

    appeared = _federation_ids(receiver_client) - before
    assert len(appeared) == 1, (
        f"expected exactly one new federation on the receiver, got {appeared!r}; "
        "the unsigned intake POST did not arrive, or something else raced it"
    )
    receiver_id = appeared.pop()
    receiver_row = _find_by_id(receiver_client, receiver_id)
    assert receiver_row and receiver_row.get("status") == STATUS_REQUESTED, (
        f"intake should land as {STATUS_REQUESTED}: {receiver_row!r}"
    )

    approved = _obj(
        receiver_client,
        "POST",
        f"/cluster/federations/{receiver_id}/approve",
        json={
            "identity_mode": "receiver_realm",
            "preshared_key": psk,
            "realm_scope": "per_federation",
        },
    )
    assert approved.get("status") == STATUS_APPROVED, f"approve failed: {approved!r}"

    state = {
        "name": name,
        "psk": psk,
        "initiator_id": initiator_id,
        "receiver_id": receiver_id,
    }
    try:
        yield state
    finally:
        for label, client, fed_id in (
            ("initiator", initiator_client, initiator_id),
            ("receiver", receiver_client, receiver_id),
        ):
            try:
                client._request("POST", f"/cluster/federations/{fed_id}/disconnect")
            except Exception as exc:  # pragma: no cover - teardown best-effort
                logger.warning("failed to disconnect %s %s: %s", label, fed_id, exc)


class TestRequestApproveLifecycle:
    def test_the_receiver_records_the_intake_and_approves_it(
        self, approved_federation: dict[str, Any], receiver_client: KamiwazaClient
    ) -> None:
        """The happy path, asserted from the receiver's own listing rather than
        the approve response, so a row that never persisted cannot pass."""
        row = _find_by_id(receiver_client, approved_federation["receiver_id"])
        assert row, "the approved federation vanished from the receiver's listing"
        assert row.get("status") == STATUS_APPROVED, f"unexpected status: {row!r}"

    def test_approving_twice_is_refused_and_keeps_the_federation(
        self, approved_federation: dict[str, Any], receiver_client: KamiwazaClient
    ) -> None:
        """The regression that matters: a second approve must not delete the row.

        Asserting only that the call errors would pass on the broken build too —
        it errored there as well, *after* rolling the federation away. Two
        assertions carry it: the federation survives, and the refusal is a 409
        naming the status. A bare 500 satisfies neither the operator nor this
        test; that is what the endpoint returned before the mapping was added.
        """
        fed_id = approved_federation["receiver_id"]

        with pytest.raises(Exception) as exc_info:
            receiver_client._request(
                "POST",
                f"/cluster/federations/{fed_id}/approve",
                json={
                    "identity_mode": "receiver_realm",
                    "preshared_key": approved_federation["psk"],
                    "realm_scope": "per_federation",
                },
            )
        refusal = str(exc_info.value)
        logger.info("second approve refused with: %s", refusal)
        assert "409" in refusal, (
            f"a refusal must be a conflict, not a server fault: {refusal}"
        )
        assert "REQUESTED" in refusal, (
            f"the refusal must name the status that blocked it: {refusal}"
        )

        survivor = _find_by_id(receiver_client, fed_id)
        assert survivor, (
            "a second approve DELETED the federation — the realm and every "
            "guest in it are now orphaned while the peer still believes the "
            "federation is live"
        )
        assert survivor.get("status") == STATUS_APPROVED, (
            f"a refused approve must leave the row untouched: {survivor!r}"
        )

    def test_approving_an_unknown_federation_is_a_clean_404(
        self, receiver_client: KamiwazaClient
    ) -> None:
        """A bogus id must not be answered with a 500 from deeper in the stack."""
        with pytest.raises(Exception) as exc_info:
            receiver_client._request(
                "POST",
                f"/cluster/federations/{uuid.uuid4()}/approve",
                json={
                    "identity_mode": "receiver_realm",
                    "preshared_key": str(uuid.uuid4()),
                },
            )
        assert "404" in str(exc_info.value) or "not found" in str(exc_info.value).lower(), (
            f"expected a not-found answer, got: {exc_info.value}"
        )
