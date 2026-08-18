"""ENG-9807 — live federation trust lifecycle through the SDK.

Four platform endpoints the SDK could not reach until now, exercised against a
real pair via ``kz.cluster``:

    rotate_preshared_key      open the dual-accept window (ENG-9501)
    complete_key_rotation     close it, retiring the outgoing key
    refresh_peer_ca           replace a rotated peer CA (ENG-9507)
    reconnect_federation      undo a local disconnect (ENG-9694)

**The refusals are the deliverable here, not the happy paths.** Three of these
calls are governed by an assertion the cluster cannot verify — ``acknowledged``
on the rotation close, ``acknowledged_fingerprint`` on the CA refresh — and a
client that silently dropped either field would sail through a happy-path-only
suite. So each acknowledgement is pinned from both sides: the call fails without
it, and the server's refusal echoes back the value the SDK actually sent.

Two refusals pin the state machine itself rather than a field. A second
``rotate`` while one window is open would overwrite the outgoing key and strand
a peer still signing with the original, and a ``reconnect`` on anything but a
DISCONNECTED row would paper over a divergence that needs the pairing flow.

Topology mirrors the onboarding + receiver_realm suites: the **initiator** is the
primary ``--live-base-url`` cluster and the **receiver** is
``--live-peer-base-url``. Gated by ``requires_two_clusters``, so it auto-
deselects on PRs without a peer rig. Serve on the per-host FQDN so istio's
Host-header routing resolves.

Rotation runs on the INITIATOR's row only, and closing a window there
deliberately desynchronises the pair: this cluster starts signing with a key the
peer has never been told about, because delivering it is the out-of-band step
the endpoint exists to bracket. That is why teardown disconnects with
``force=true`` — a graceful disconnect is itself PSK-signed, so the peer would
refuse it and both rows would be left PAIRED with a dead mesh between them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any, Iterator

import pytest

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import KamiwazaError

logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
    pytest.mark.requires_two_clusters,
    pytest.mark.requires_receiver_realm,
]

STATUS_PAIRED = "PAIRED"
STATUS_DISCONNECTED = "DISCONNECTED"

# Never sent with a matching acknowledgement. ``refresh_peer_ca`` does not parse
# the PEM — it fingerprints the text and stores it verbatim — so an accepted
# refresh with this payload would replace a live federation's trust root with
# garbage. Every test below supplies an acknowledgement that cannot match.
FAKE_CA_PEM = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIBkTCB+wIJAKxxxxxxxxxxeng9807LiveTrustLifecycleFixtureNotARealCA\n"
    "-----END CERTIFICATE-----\n"
)
OTHER_CA_PEM = FAKE_CA_PEM.replace("eng9807", "eng9807b")


def _fingerprint(pem: str) -> str:
    """The server's fingerprint, recomputed independently.

    SHA-256 over the whitespace-normalised PEM (``cluster/federation.py::
    peer_ca_fingerprint``). Recomputed rather than read back so the refusal
    payload can be checked against what this test sent — which is what proves
    ``ca_pem`` reached the server at all.
    """
    return hashlib.sha256("".join(pem.split()).encode("utf-8")).hexdigest()


def _obj(client: KamiwazaClient, method: str, path: str, **kwargs) -> dict[str, Any]:
    body = client._request(method, path, **kwargs)
    assert isinstance(body, dict), f"{method} {path} returned {body!r}, expected object"
    return body


def _row(client: KamiwazaClient, federation_id: str) -> dict[str, Any]:
    return _obj(client, "GET", f"/cluster/federations/{federation_id}")


def _refusal(exc: KamiwazaError) -> tuple[int | None, str | None, dict[str, Any]]:
    """Status, machine-readable reason, and the full detail of a refusal.

    The reason codes are the contract (``outcome_reason_codes`` on each route),
    so tests assert on those rather than on prose — and a bare 500, which is
    what these endpoints returned before the mappings existed, satisfies
    neither half.
    """
    body = exc.body if isinstance(exc.body, dict) else {}
    detail = body.get("detail")
    detail = detail if isinstance(detail, dict) else {}
    return exc.status_code, detail.get("reason"), detail


def _force_disconnect(client: KamiwazaClient, federation_id: str, label: str) -> None:
    try:
        client._request(
            "POST",
            f"/cluster/federations/{federation_id}/disconnect",
            params={"force": "true"},
        )
    except Exception as exc:  # pragma: no cover - teardown best-effort
        logger.warning("failed to disconnect %s %s: %s", label, federation_id, exc)


def _close_any_open_window(client: KamiwazaClient, federation_id: str) -> None:
    """Return the federation to 'no rotation in flight', whatever state it is in.

    Passing ``acknowledged=True`` here is a harness assertion, not an operator
    one — there is no peer to deliver to, and the federation is torn down at the
    end of the module. Never copy this shape into anything an operator runs.

    Swallows only the refusal that means the work is already done; anything else
    is a real failure and must not be hidden behind a cleanup helper.
    """
    try:
        client.cluster.complete_key_rotation(federation_id, acknowledged=True)
    except KamiwazaError as exc:
        _status, reason, _detail = _refusal(exc)
        if reason != "no_rotation_in_flight":
            raise


@pytest.fixture(scope="module")
def initiator_client(live_kamiwaza_session_client: KamiwazaClient) -> KamiwazaClient:
    return live_kamiwaza_session_client


@pytest.fixture(scope="module")
def receiver_client(live_kamiwaza_peer_client: KamiwazaClient) -> KamiwazaClient:
    return live_kamiwaza_peer_client


@pytest.fixture(scope="module")
def paired_federation(
    initiator_client: KamiwazaClient,
    receiver_client: KamiwazaClient,
    live_peer_base_url: str,
) -> Iterator[dict[str, str]]:
    """One PAIRED federation for the module. Pairing is the expensive step.

    ``realm_scope`` is set on BOTH sides: the create-time identity stamp is
    resolved per row, so without it the initiator's row falls through to the
    legacy source-trusted peer_kc mode and is refused unless
    ``ALLOW_UNTRUSTED_FEDERATION`` is on.
    """
    name = f"eng9807-trust-{uuid.uuid4().hex[:8]}"
    pair_psk = str(uuid.uuid4())

    receiver_fed = receiver_client.federations.pair(
        name=name,
        role="receiver",
        preshared_key=pair_psk,
        realm_scope="per_federation",
    )
    receiver_id = str(receiver_fed.id)
    try:
        initiator_fed = initiator_client.federations.pair(
            name=name,
            role="initiator",
            remote_url=live_peer_base_url,
            preshared_key=pair_psk,
            realm_scope="per_federation",
        )
    except Exception:
        _force_disconnect(receiver_client, receiver_id, "receiver")
        raise

    state = {
        "name": name,
        "initiator_id": str(initiator_fed.id),
        "receiver_id": receiver_id,
    }
    status = _row(initiator_client, state["initiator_id"]).get("status")
    assert status == STATUS_PAIRED, (
        f"the handshake left the initiator row {status!r}; rotation and reconnect "
        "both refuse anything but PAIRED, so nothing below would be meaningful"
    )
    try:
        yield state
    finally:
        for label, client, fed_id in (
            ("initiator", initiator_client, state["initiator_id"]),
            ("receiver", receiver_client, receiver_id),
        ):
            _force_disconnect(client, fed_id, label)


@pytest.fixture
def federation_id(paired_federation: dict[str, str]) -> str:
    """The initiator's row — the one every mutation below runs against."""
    return paired_federation["initiator_id"]


@pytest.fixture
def open_window(
    initiator_client: KamiwazaClient, federation_id: str
) -> Iterator[dict[str, Any]]:
    """A freshly opened rotation window, closed again on the way out.

    Function-scoped so every test starts from a known rotation state and none of
    them depends on the order of the others — the state machine's whole job is
    to behave differently depending on whether a window is open.
    """
    opened = initiator_client.cluster.rotate_preshared_key(federation_id)
    try:
        yield opened
    finally:
        _close_any_open_window(initiator_client, federation_id)


class TestPresharedKeyRotation:
    """ENG-9501 — the dual-accept window, and the two states it refuses from."""

    def test_opening_a_window_returns_the_new_key_exactly_once(
        self,
        open_window: dict[str, Any],
        initiator_client: KamiwazaClient,
        federation_id: str,
    ) -> None:
        """The plaintext key is returned here and stored nowhere retrievable.

        Asserting only that a key came back would pass on an implementation that
        also persisted it readable — so the federation row is read back and the
        key must not be in it. The operator's copy of this value is the only one.
        """
        assert open_window.get("reason") == "rotation_opened", (
            f"unexpected rotation outcome: {open_window!r}"
        )
        new_key = open_window.get("preshared_key")
        assert new_key, f"rotation must return the new key once: {open_window!r}"

        row = _row(initiator_client, federation_id)
        assert new_key not in json.dumps(row), (
            "the plaintext pre-shared key is readable from the federation row; "
            "it is supposed to survive only in the operator's hands"
        )

    def test_a_second_rotation_is_refused_while_one_is_in_flight(
        self,
        open_window: dict[str, Any],
        initiator_client: KamiwazaClient,
        federation_id: str,
    ) -> None:
        """The load-bearing refusal of the open half.

        Opening twice would overwrite ``preshared_key_previous`` with the key
        minted moments ago, silently stranding a peer still signing with the
        original and leaving no record of which key it holds. The refusal is what
        makes 'deliver, then close' the only path forward.
        """
        with pytest.raises(KamiwazaError) as exc_info:
            initiator_client.cluster.rotate_preshared_key(federation_id)

        status, reason, detail = _refusal(exc_info.value)
        assert (status, reason) == (409, "rotation_already_in_flight"), (
            f"expected a conflict naming the open window, got {status} {detail!r}"
        )

    def test_closing_without_the_acknowledgement_is_refused(
        self,
        open_window: dict[str, Any],
        initiator_client: KamiwazaClient,
        federation_id: str,
    ) -> None:
        """``acknowledged=False`` must refuse, and must leave the window open.

        This is the half of the acknowledgement contract a happy-path test
        cannot see. Paired with the close below it also pins the SDK's body: a
        client that dropped the field entirely would fail there, and one that
        hard-coded it True would fail here.
        """
        with pytest.raises(KamiwazaError) as exc_info:
            initiator_client.cluster.complete_key_rotation(
                federation_id, acknowledged=False
            )

        status, reason, detail = _refusal(exc_info.value)
        assert (status, reason) == (400, "rotation_acknowledgement_required"), (
            f"expected a refusal naming the acknowledgement, got {status} {detail!r}"
        )

        # A refused close must not have retired anything — the window is still
        # open, which the in-flight refusal above is the cheapest way to prove.
        with pytest.raises(KamiwazaError) as still_open:
            initiator_client.cluster.rotate_preshared_key(federation_id)
        assert _refusal(still_open.value)[1] == "rotation_already_in_flight", (
            "a refused close retired the outgoing key anyway — the peer is now "
            "cut off by a call that reported failure"
        )

    def test_closing_with_the_acknowledgement_retires_the_outgoing_key(
        self,
        open_window: dict[str, Any],
        initiator_client: KamiwazaClient,
        federation_id: str,
    ) -> None:
        """The close succeeds only if ``acknowledged`` actually reached the wire.

        The server reads ``bool(body.get("acknowledged"))``, so an SDK that
        dropped the field would land on the 400 above instead — which is exactly
        the silent-drop failure this file exists to catch.
        """
        closed = initiator_client.cluster.complete_key_rotation(
            federation_id, acknowledged=True
        )
        assert closed.get("reason") == "rotation_closed", (
            f"unexpected close outcome: {closed!r}"
        )

    def test_closing_with_no_rotation_open_is_refused(
        self, initiator_client: KamiwazaClient, federation_id: str
    ) -> None:
        """Closing a window that was never opened is a conflict, not a no-op.

        A silent success here would tell an operator mid-incident that they had
        retired a key they had not, which is the worst possible answer about the
        state of a trust relationship.
        """
        _close_any_open_window(initiator_client, federation_id)

        with pytest.raises(KamiwazaError) as exc_info:
            initiator_client.cluster.complete_key_rotation(
                federation_id, acknowledged=True
            )

        status, reason, detail = _refusal(exc_info.value)
        assert (status, reason) == (409, "no_rotation_in_flight"), (
            f"expected a conflict naming the missing window, got {status} {detail!r}"
        )


class TestPeerCARefresh:
    """ENG-9507 — the acknowledgement that forces an out-of-band comparison.

    Only refusals are exercised. The endpoint does not parse the PEM — it
    fingerprints the text and stores it verbatim — so a successful refresh with
    a synthetic CA would replace a live federation's trust root with a value no
    peer certificate can chain to.
    """

    def test_refresh_without_an_acknowledgement_is_refused(
        self, initiator_client: KamiwazaClient, federation_id: str
    ) -> None:
        """Empty acknowledgement refuses — and the refusal hands back the
        fingerprint to verify, which is the whole point of the speed bump.

        The echoed fingerprint doubles as proof the SDK sent ``ca_pem``: a
        dropped PEM would be refused ``peer_ca_required`` instead.
        """
        with pytest.raises(KamiwazaError) as exc_info:
            initiator_client.cluster.refresh_peer_ca(
                federation_id, ca_pem=FAKE_CA_PEM, acknowledged_fingerprint=""
            )

        status, reason, detail = _refusal(exc_info.value)
        assert (status, reason) == (400, "fingerprint_acknowledgement_required"), (
            f"expected a refusal naming the acknowledgement, got {status} {detail!r}"
        )
        assert detail.get("fingerprint") == _fingerprint(FAKE_CA_PEM), (
            "the refusal must carry the fingerprint of the CA that was sent, so "
            f"the operator can compare it out of band: {detail!r}"
        )

    def test_refresh_with_a_mismatched_acknowledgement_is_refused(
        self, initiator_client: KamiwazaClient, federation_id: str
    ) -> None:
        """A fingerprint that does not match the CA is a 409, not a retry.

        From this side a substitution and a legitimate rotation are
        indistinguishable, so the answer is 're-verify out of band'. This also
        pins the second half of the SDK body: dropping
        ``acknowledged_fingerprint`` would land on the 400 above instead of here.
        """
        with pytest.raises(KamiwazaError) as exc_info:
            initiator_client.cluster.refresh_peer_ca(
                federation_id,
                ca_pem=FAKE_CA_PEM,
                acknowledged_fingerprint=_fingerprint(OTHER_CA_PEM),
            )

        status, reason, detail = _refusal(exc_info.value)
        assert (status, reason) == (409, "fingerprint_acknowledgement_mismatch"), (
            f"expected a conflict on the mismatch, got {status} {detail!r}"
        )
        assert detail.get("fingerprint") == _fingerprint(FAKE_CA_PEM), (
            f"the refusal must name the supplied CA's real fingerprint: {detail!r}"
        )


class TestReconnect:
    """ENG-9694 — the reachable inverse of a disconnect, and its one gate."""

    def test_reconnect_is_refused_on_a_paired_federation(
        self, initiator_client: KamiwazaClient, federation_id: str
    ) -> None:
        """Narrow by design: reconnect reverses a disconnect and nothing else.

        A best-effort repair of a PAIRED-but-broken row would hide exactly the
        divergences (peer rotated keys, tore down its realm) that the pairing
        flow exists to handle.
        """
        with pytest.raises(KamiwazaError) as exc_info:
            initiator_client.cluster.reconnect_federation(federation_id)

        status, reason, detail = _refusal(exc_info.value)
        assert (status, reason) == (409, "federation_not_disconnected"), (
            f"expected a conflict on the live federation, got {status} {detail!r}"
        )
        assert detail.get("status") == STATUS_PAIRED, (
            f"the refusal must name the status that blocked it: {detail!r}"
        )

    def test_reconnecting_an_unknown_federation_is_a_clean_404(
        self, initiator_client: KamiwazaClient
    ) -> None:
        """A bogus id must not be answered with a 500 from deeper in the stack."""
        with pytest.raises(KamiwazaError) as exc_info:
            initiator_client.cluster.reconnect_federation(str(uuid.uuid4()))

        status, reason, _detail = _refusal(exc_info.value)
        assert status == 404, f"expected a not-found answer, got {status}"
        assert reason == "federation_not_found", f"unexpected reason: {reason!r}"

    def test_a_disconnected_federation_can_be_reconnected(
        self, initiator_client: KamiwazaClient, federation_id: str
    ) -> None:
        """The round trip, last because it is the one test that moves the row.

        Runs against a forced disconnect so the peer is never told: this is the
        local half, and re-admitting the peer's guests is the property under
        test. It restores PAIRED before returning, so the refusal tests above
        remain valid whatever order they run in.
        """
        _force_disconnect(initiator_client, federation_id, "initiator")
        assert _row(initiator_client, federation_id).get("status") == (
            STATUS_DISCONNECTED
        ), "the forced disconnect did not take; nothing below tests reconnect"

        result = initiator_client.cluster.reconnect_federation(federation_id)

        assert isinstance(result.get("restored"), int), (
            f"reconnect must report how many guests it re-admitted: {result!r}"
        )
        assert _row(initiator_client, federation_id).get("status") == STATUS_PAIRED, (
            "reconnect reported success but the row is still disconnected"
        )
