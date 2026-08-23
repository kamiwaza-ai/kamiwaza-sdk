"""ENG-10082 — live, peer-proven federation trust lifecycle through the SDK.

The rotation case exercises the complete safe protocol against a real pair:
stage, status recovery, abort, divergent peer-stage recovery, adoption,
peer-first activation, and peer-first completion. Signed ping remains reachable
before, during, and after the transition. Retried adoption, activation,
completion, and abort pin lost-response idempotency.

The adjacent CA and reconnect refusals remain explicit. A caller-provided
fingerprint acknowledgement can only prove that the operator looked at a CA;
it is never substituted for the peer-owned evidence that authorizes PSK
activation and K1 retirement.

Topology mirrors the onboarding + receiver_realm suites: the **initiator** is the
primary ``--live-base-url`` cluster and the **receiver** is
``--live-peer-base-url``. Gated by ``requires_two_clusters``, so it auto-
deselects on PRs without a peer rig. Serve on the per-host FQDN so istio's
Host-header routing resolves.

The initiator mints K2 and the receiver adopts the exact out-of-band key and
fingerprint. Core then supplies the peer-signed activation/completion evidence;
the SDK never turns a local boolean into a successful rotation.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any, Iterator
from urllib.parse import urlparse

import pytest

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import KamiwazaError

from ._federation_psk_rotation import (
    RotationPair,
    exercise_peer_proven_rotation,
    settle_pair,
)

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


def _host_of(url: str) -> str:
    host = urlparse(url).hostname
    assert host, f"could not parse a hostname out of {url!r}"
    return host


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


def _delete_federation(client: KamiwazaClient, federation_id: str, label: str) -> None:
    try:
        client._request("DELETE", f"/cluster/federations/{federation_id}")
    except Exception as exc:  # pragma: no cover - teardown best-effort
        logger.warning("failed to delete %s %s: %s", label, federation_id, exc)


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
    """Create one PAIRED federation; both rows need ``realm_scope``.

    Otherwise the initiator falls back to gated legacy ``peer_kc`` mode.
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
            callback_hostname=_host_of(initiator_client.base_url),
            realm_scope="per_federation",
        )
    except Exception:
        _delete_federation(receiver_client, receiver_id, "receiver")
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
            _delete_federation(client, fed_id, label)


@pytest.fixture
def federation_id(paired_federation: dict[str, str]) -> str:
    """The initiator's row — the one every mutation below runs against."""
    return paired_federation["initiator_id"]


@pytest.fixture
def settled_rotation_pair(
    paired_federation: dict[str, str],
    initiator_client: KamiwazaClient,
    receiver_client: KamiwazaClient,
) -> Iterator[RotationPair]:
    """Expose one pair and restore both rows to a closed rotation state."""
    pair = RotationPair(
        initiator=initiator_client,
        receiver=receiver_client,
        initiator_id=paired_federation["initiator_id"],
        receiver_id=paired_federation["receiver_id"],
    )
    primary_error: BaseException | None = None
    try:
        yield pair
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            settle_pair(pair)
        except Exception as cleanup_error:
            if primary_error is None:
                raise
            logger.exception(
                "rotation cleanup failed while preserving test failure: %r",
                cleanup_error,
            )


def test_peer_proven_rotation_recovers_and_keeps_signed_traffic_live(
    settled_rotation_pair: RotationPair,
) -> None:
    """Exercise recovery, convergence, peer-first switch, and idempotency."""
    exercise_peer_proven_rotation(settled_rotation_pair)


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
        assert (status, reason) == (
            400,
            "fingerprint_acknowledgement_required",
        ), f"expected a refusal naming the acknowledgement, got {status} {detail!r}"
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
        assert (status, reason) == (
            409,
            "fingerprint_acknowledgement_mismatch",
        ), f"expected a conflict on the mismatch, got {status} {detail!r}"
        assert detail.get("fingerprint") == _fingerprint(
            FAKE_CA_PEM
        ), f"the refusal must name the supplied CA's real fingerprint: {detail!r}"


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
        assert (status, reason) == (
            409,
            "federation_not_disconnected",
        ), f"expected a conflict on the live federation, got {status} {detail!r}"
        assert (
            detail.get("status") == STATUS_PAIRED
        ), f"the refusal must name the status that blocked it: {detail!r}"

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

        assert isinstance(
            result.get("restored"), int
        ), f"reconnect must report how many guests it re-admitted: {result!r}"
        assert (
            _row(initiator_client, federation_id).get("status") == STATUS_PAIRED
        ), "reconnect reported success but the row is still disconnected"
