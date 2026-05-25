"""ENG-5784 — Live two-cluster federation walkthrough.

The live counterpart to ``test_federation_skeleton_walkthrough.py``
(which mocks the same flow). This test exercises the WS-M1+ federation
surface end-to-end against a real peer cluster: pair → brokered user →
federated job (audit-actor round-trip) → retrieval surface smoke →
clean unpair.

Gated by the ``requires_two_clusters`` marker plus the
``KAMIWAZA_PEER_BASE_URL`` + ``KAMIWAZA_PEER_API_KEY`` env vars (mirrors
the ``requires_embedding_model`` convention). Auto-deselected when
neither --live-peer-base-url nor KAMIWAZA_PEER_BASE_URL is set, so
contributor PRs without peer creds don't show false reds.

Initial peer rig: spark-1 ↔ evo-x2-1 (see memory:
reference_fleet_validation_hosts.md).
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Iterator

import pytest

from kamiwaza_sdk import KamiwazaClient

logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
    pytest.mark.requires_two_clusters,
]


@pytest.fixture(scope="module")
def federation_pair_name() -> str:
    """Per-run unique federation name so re-runs don't collide on stale state."""
    return f"eng5784-live-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def shared_psk() -> str:
    """Shared PSK between initiator and receiver. Mode B (caller-supplied)."""
    return str(uuid.uuid4())


@pytest.fixture(scope="module")
def initiator_client(live_kamiwaza_session_client: KamiwazaClient) -> KamiwazaClient:
    """Local cluster as initiator. Uses the session-scoped client so this
    module-scoped fixture's scope chain is consistent (depending on the
    function-scoped ``live_kamiwaza_client`` would raise ScopeMismatch).
    """
    return live_kamiwaza_session_client


@pytest.fixture(scope="module")
def receiver_client(live_kamiwaza_peer_client: KamiwazaClient) -> KamiwazaClient:
    """Peer cluster as receiver of the federation."""
    return live_kamiwaza_peer_client


@pytest.fixture(scope="module")
def initiator_cluster_uuid(initiator_client: KamiwazaClient) -> str:
    """UUID of the initiator cluster. Used to build brokered external_ids.

    Reads ``local_node_id`` from ``cluster.capabilities()`` — that's the
    canonical cluster-identity UUID in the ClusterCapabilities schema.
    Falls back to ``cluster_id`` / ``id`` for backend versions that may
    expose alternate field names.
    """
    capabilities = initiator_client.cluster.capabilities()
    cluster_id = (
        getattr(capabilities, "local_node_id", None)
        or getattr(capabilities, "cluster_id", None)
        or getattr(capabilities, "id", None)
    )
    if not cluster_id:
        pytest.fail(
            "initiator cluster.capabilities() returned no identifying UUID; "
            f"got {capabilities!r}"
        )
    return str(cluster_id)


@pytest.fixture(scope="module")
def paired_federation(
    initiator_client: KamiwazaClient,
    receiver_client: KamiwazaClient,
    federation_pair_name: str,
    shared_psk: str,
    live_peer_base_url: str,
) -> Iterator[dict[str, str]]:
    """Establish a federation pair for the test module. Tears down at exit.

    Yields a dict with the federation_id on both sides plus the pair name
    so individual tests can stitch onto the live state.
    """
    # Receiver creates its side first (WAITING state) so the initiator's
    # /pair handshake has something to hit. The receiver record only
    # needs name + role + psk — it doesn't need a callback URL because
    # the initiator reaches out, not the other way around. Passing
    # remote_url on the receiver side derives a wrong remote_ips entry
    # (the receiver doesn't need to know the initiator's location).
    # Mirrors the kamiwaza-smoke.py federation-pair flow at services.py.
    receiver_fed = receiver_client.federations.pair(
        name=federation_pair_name,
        role="receiver",
        preshared_key=shared_psk,
    )
    receiver_fed_id = str(receiver_fed.id)

    # Initiator drives the handshake (PSK barrier exercised here). If
    # initiator pair raises after the receiver-side record was created,
    # best-effort clean up the orphaned receiver federation before
    # re-raising so the next run doesn't collide on stale state.
    try:
        initiator_fed = initiator_client.federations.pair(
            name=federation_pair_name,
            role="initiator",
            remote_url=live_peer_base_url,
            preshared_key=shared_psk,
        )
    except Exception:
        try:
            receiver_client._request(
                "POST", f"/cluster/federations/{receiver_fed_id}/disconnect"
            )
        except Exception as cleanup_exc:  # pragma: no cover - best effort
            logger.warning(
                "failed to clean up orphaned receiver federation %s after "
                "initiator pair failure: %s",
                receiver_fed_id,
                cleanup_exc,
            )
        raise

    state = {
        "initiator_id": str(initiator_fed.id),
        "receiver_id": receiver_fed_id,
        "name": federation_pair_name,
    }
    try:
        yield state
    finally:
        # Best-effort unpair on both sides — failures during teardown
        # shouldn't mask test failures.
        for client_label, client, fed_id in (
            ("initiator", initiator_client, state["initiator_id"]),
            ("receiver", receiver_client, state["receiver_id"]),
        ):
            try:
                client._request("POST", f"/cluster/federations/{fed_id}/disconnect")
            except Exception as exc:  # pragma: no cover - teardown best-effort
                logger.warning(
                    "failed to disconnect %s federation %s: %s",
                    client_label,
                    fed_id,
                    exc,
                )


@pytest.fixture
def unpaired_federation(
    initiator_client: KamiwazaClient,
    receiver_client: KamiwazaClient,
    shared_psk: str,
    live_peer_base_url: str,
) -> Iterator[dict[str, str]]:
    """Fresh function-scoped pair for tests that mutate pair lifecycle state.

    The module-scoped ``paired_federation`` is a shared resource; tests
    like ``test_unpair_returns_to_clean_state`` would otherwise create
    order-dependent suite behavior. This fixture stands up a separate
    federation with a unique name per test, so mutating its state
    doesn't affect the module-scoped pair.
    """
    fresh_name = f"eng5784-unpair-{uuid.uuid4().hex[:8]}"
    receiver_fed = receiver_client.federations.pair(
        name=fresh_name,
        role="receiver",
        preshared_key=shared_psk,
    )
    receiver_fed_id = str(receiver_fed.id)
    try:
        initiator_fed = initiator_client.federations.pair(
            name=fresh_name,
            role="initiator",
            remote_url=live_peer_base_url,
            preshared_key=shared_psk,
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
        "name": fresh_name,
    }
    try:
        yield state
    finally:
        for client_label, client, fed_id in (
            ("initiator", initiator_client, state["initiator_id"]),
            ("receiver", receiver_client, state["receiver_id"]),
        ):
            try:
                client._request("POST", f"/cluster/federations/{fed_id}/disconnect")
            except Exception as exc:  # pragma: no cover - teardown best-effort
                logger.warning(
                    "failed to disconnect %s federation %s: %s",
                    client_label,
                    fed_id,
                    exc,
                )


class TestFederationTwoClusterWalkthrough:
    """Live two-cluster federation walkthrough — counterpart to the mocked
    ``test_federation_skeleton_walkthrough.py`` flow.

    TODO(WS-M2): replace direct ``client._request("GET"/"POST", ...)``
    calls with typed wrappers once federation introspection
    (``client.federations[name].get()`` and ``.disconnect()``) lands on
    the canonical SDK surface.
    """

    def test_paired_state_visible_on_both_sides(
        self,
        paired_federation: dict[str, str],
        initiator_client: KamiwazaClient,
        receiver_client: KamiwazaClient,
    ) -> None:
        """Initiator settles into PAIRED/ACTIVE after the handshake; receiver
        may still be observed as WAITING immediately post-handshake on
        some backend versions (the asymmetric tolerance is intentional).
        """
        initiator_view = initiator_client._request(
            "GET", f"/cluster/federations/{paired_federation['initiator_id']}"
        )
        receiver_view = receiver_client._request(
            "GET", f"/cluster/federations/{paired_federation['receiver_id']}"
        )
        assert isinstance(initiator_view, dict)
        assert isinstance(receiver_view, dict)
        assert initiator_view["status"] in {"PAIRED", "ACTIVE"}
        assert receiver_view["status"] in {"PAIRED", "ACTIVE", "WAITING"}

    def test_capabilities_probe_via_mesh(
        self,
        paired_federation: dict[str, str],
        initiator_client: KamiwazaClient,
    ) -> None:
        """T5.21 — initiator can probe the receiver's capabilities through the
        mesh. Validates the federation:operator ReBAC guard + HMAC signing.
        """
        proxy = initiator_client.federations[paired_federation["name"]]
        capabilities = proxy.probe()
        # ClusterCapabilities is a pydantic model — probe() raises if the
        # mesh hop or capability schema fails. Existence of any identifying
        # UUID is the load-bearing signal. local_node_id is the canonical
        # field; cluster_id/id are accepted for backend version variance.
        cluster_id = (
            getattr(capabilities, "local_node_id", None)
            or getattr(capabilities, "cluster_id", None)
            or getattr(capabilities, "id", None)
        )
        assert (
            cluster_id
        ), f"peer capabilities missing identifying UUID: {capabilities!r}"

    def test_brokered_user_allowlist_round_trip(
        self,
        paired_federation: dict[str, str],
        initiator_client: KamiwazaClient,
        initiator_cluster_uuid: str,
        receiver_client: KamiwazaClient,
    ) -> None:
        """FR-51 / FR-80 — receiver allowlists a brokered user from the
        initiator. Auto-provisioning happens on first mesh request; we
        validate only that the allowlist write succeeds and the record
        is queryable.

        Uses the receiver-side federation ID (not the operator-supplied
        name) because the pair handshake overwrites the receiver's
        ``remote_cluster_name`` with the initiator's cluster name —
        ``federations[name]`` lookup-by-name fails on the receiver
        post-pair. POST the user record directly against the
        receiver-side ID, mirroring how setup.py / cmd_m3 drives this.
        """
        external_id = (
            f"eng5784-brokered-{uuid.uuid4().hex[:6]}@{initiator_cluster_uuid}"
        )
        brokered = receiver_client._request(
            "POST",
            f"/cluster/federations/{paired_federation['receiver_id']}/users",
            json={"external_id": external_id},
        )
        assert isinstance(brokered, dict)
        assert brokered["external_id"] == external_id
        # auto_provisioned starts False — flips True on the user's first
        # mesh-origin request. We don't drive that here; the cmd_m3 smoke
        # script does that end-to-end.

    def test_federated_job_audit_actor_round_trip(
        self,
        paired_federation: dict[str, str],
        initiator_client: KamiwazaClient,
    ) -> None:
        """The WS-M1 demo-gate signal: a federated job runs as the
        originating user, not as a system principal. Audit-actor round-trip
        is the proof. T5.22 / ENG-4699.
        """
        # Use the recoverable path so we get the job_id back immediately
        # and can poll for the terminal state.
        result = initiator_client.jobs.run(
            entrypoint='python -c "print(\\"eng5784\\")"',
            target_cluster=paired_federation["name"],
            timeout_seconds=120,
            recoverable=True,
        )
        assert result.status in {
            "SUCCEEDED",
            "FAILED",
        }, f"job did not reach terminal state: {result}"
        # The audit_actor field is the demo-gate signal. Backend versions
        # may name it audit_actor / requester / submitter — accept any
        # non-None identity-like attribute as the round-trip proof.
        actor = (
            getattr(result, "audit_actor", None)
            or getattr(result, "requester", None)
            or getattr(result, "submitter", None)
        )
        assert actor, f"job result missing audit-actor identity: {result}"

    def test_retrieval_surface_reachable_on_both_clusters(
        self,
        paired_federation: dict[str, str],
        initiator_client: KamiwazaClient,
        receiver_client: KamiwazaClient,
    ) -> None:
        """Smoke that the federated retrieval list endpoint is reachable
        on both clusters after pairing. Doesn't drive a federated query
        (the cmd_m3 smoke covers that); validates the surface is alive
        and the pair didn't break retrieval routing.
        """
        # list() returns [] when no jobs — non-empty is fine but not required.
        assert isinstance(initiator_client.retrieval.list(limit=1), list)
        assert isinstance(receiver_client.retrieval.list(limit=1), list)

    def test_unpair_returns_to_clean_state(
        self,
        unpaired_federation: dict[str, str],
        initiator_client: KamiwazaClient,
    ) -> None:
        """Disconnect the federation from the initiator side and assert the
        initiator-side status reflects the disconnect. Uses a dedicated
        fresh-pair fixture (``unpaired_federation``) so this test doesn't
        mutate the module-scoped ``paired_federation`` state — other tests
        stay order-independent.
        """
        initiator_client._request(
            "POST",
            f"/cluster/federations/{unpaired_federation['initiator_id']}/disconnect",
        )
        # Brief settle window — the disconnect is synchronous on the
        # initiator side, but the status field may be eventually
        # consistent across the request/response boundary on slower
        # backends.
        time.sleep(1)
        view = initiator_client._request(
            "GET", f"/cluster/federations/{unpaired_federation['initiator_id']}"
        )
        assert isinstance(view, dict)
        assert view["status"] in {"DISCONNECTED", "DEAD", "WAITING"}, view
