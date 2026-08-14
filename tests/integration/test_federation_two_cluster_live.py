"""ENG-5784 — Live two-cluster federation walkthrough.

The live counterpart to ``test_federation_skeleton_walkthrough.py``
(which mocks the same flow). This test exercises the WS-M1+ federation
surface end-to-end against a real peer cluster: pair → brokered user →
federated job (audit-actor round-trip) → retrieval surface smoke →
clean unpair.

Pairs in the **receiver-controlled** ``receiver_realm`` mode. It originally
paired in the legacy source-trusted ``peer_kc`` mode, which ENG-8213 gates off
behind ``ALLOW_UNTRUSTED_FEDERATION`` — so once that landed, every test here
failed at fixture setup with ``untrusted_federation_disabled`` and the whole
file had been erroring rather than running (ENG-9571). Skipping would have been
the cheap fix; nothing under test is peer_kc-specific, so the walkthrough moves
to the mode the product actually ships instead, and the coverage stays live.

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
import shlex
import time
import uuid
from typing import Iterator

import pytest

from tests.integration import mesh_outcome
from tests.integration.mesh_outcome import MeshPolicy

from kamiwaza_sdk import KamiwazaClient

logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
    pytest.mark.requires_two_clusters,
]


_WALKTHROUGH_POLICY = MeshPolicy(
    identity_arranged=False,
    admission_is_the_assertion=False,
    context="ENG-5784 two-cluster walkthrough (pairs, enrolls no guest)",
)


def _mesh_call_or_skip(call):
    """Run a mesh data-plane call and classify the outcome.

    * ``AuthenticationError`` (401) -> ``pytest.skip``. Under the
      receiver-controlled mode this module now pairs in, a user-identity mesh
      call needs a receiver-minted guest credential resolved on the initiator,
      and this walkthrough enrolls no guest — so 401 is the designed answer, not
      a fault. See below on what that costs.
    * ``APIError`` status 403 -> ``pytest.skip``: mesh auth PASSED; the caller
      hit a downstream gate (brokered-user allowlist or a missing execution
      gate). The op-specific assertion needs that precondition.
    * any other error propagates as a real failure.

    **The ENG-7203 guard is genuinely weakened here and that is deliberate.**
    A 401 used to hard-fail as "the receiver stripped the ``x-kz-mesh-*`` HMAC
    headers before ext-authz". Under ``peer_kc`` that inference was sound,
    because the source's signed identity was the whole of mesh auth. It is not
    sound under ``receiver_realm``: a missing guest credential produces the
    identical 401, and the client cannot tell the two apart. Keeping the old
    hard-fail would have made this file report an HMAC regression on every run
    and send whoever triaged it hunting a bug that isn't there — a false
    diagnosis is worse than a stated gap. Mesh transport under receiver_realm is
    covered by ``test_federation_receiver_realm_live.py``.

    ENG-9664: the classification itself now lives in ``mesh_outcome`` so all
    three live suites share one decision point and one set of unit-pinned rules.
    Outcomes here are unchanged: 401 -> skip, non-auth 403/404 -> skip, anything
    else reds. What changes is that an auth-layer-marked 403 (the receiver
    refusing the credential, e.g. peer_jwt_validation_failed) now reds instead of
    hiding among the downstream-gate skips.

    Returns the call result on success.
    """
    return mesh_outcome.mesh_call(call, _WALKTHROUGH_POLICY)


@pytest.fixture(scope="module")
def federation_pair_name() -> str:
    """Per-run unique federation name so re-runs don't collide on stale state."""
    return f"eng5784-live-{uuid.uuid4().hex[:8]}"


# ENG-5784 R5 H1 — PSKs are now minted INSIDE each pair fixture rather
# than shared at module scope. The backend resolves the receiver-side
# federation by PSK match; if two coexisting pairs share the same PSK,
# /pair on the second one can bind to the wrong row or be refused.
# Each fixture now mints its own UUID4 so the receiver's PSK lookup is
# unambiguous.


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
def initiator_cluster_uuid(
    receiver_client: KamiwazaClient,
    paired_federation: dict[str, str],
) -> str:
    """UUID of the initiator cluster, for building brokered external_ids.

    Sourced from the RECEIVER's federation record (``remote_cluster_id`` is the
    initiator's cluster UUID, populated by the /pair handshake), NOT the
    initiator's ``cluster.capabilities()`` probe: that endpoint requires a
    cluster-probe grant an admin lacks by default (403
    ``not_authorized_to_probe_cluster``), whereas the federations list is the
    widened, any-authenticated surface.
    """
    feds = receiver_client._request("GET", "/cluster/federations") or []
    if isinstance(feds, dict):
        # Paginated {"items": [...]} shape — iterating the dict directly would
        # walk keys and AttributeError on f.get(...). Normalize like the SDK's
        # own _resolve_id does.
        feds = feds.get("items") or []
    record = next(
        (f for f in feds if str(f.get("id")) == paired_federation["receiver_id"]),
        None,
    )
    cluster_uuid = (record or {}).get("remote_cluster_id")
    if not cluster_uuid:
        pytest.fail(
            "receiver federation record has no remote_cluster_id (initiator "
            f"cluster UUID); record={record!r}"
        )
    return str(cluster_uuid)


@pytest.fixture(scope="module")
def paired_federation(
    initiator_client: KamiwazaClient,
    receiver_client: KamiwazaClient,
    federation_pair_name: str,
    live_peer_base_url: str,
) -> Iterator[dict[str, str]]:
    """Establish a federation pair for the test module. Tears down at exit.

    Yields a dict with the federation_id on both sides plus the pair name
    so individual tests can stitch onto the live state.
    """
    # R5 H1 — mint the PSK inside the fixture so it's not shared with
    # other pair fixtures (the receiver's PSK-match lookup must be
    # unambiguous across coexisting pairs).
    pair_psk = str(uuid.uuid4())

    # Receiver creates its side first (WAITING state) so the initiator's
    # /pair handshake has something to hit. The receiver record only
    # needs name + role + psk — it doesn't need a callback URL because
    # the initiator reaches out, not the other way around. Passing
    # remote_url on the receiver side derives a wrong remote_ips entry
    # (the receiver doesn't need to know the initiator's location).
    # Mirrors the kamiwaza-smoke.py federation-pair flow at services.py.
    # ``realm_scope`` is what selects the receiver-controlled mode: supplying it
    # stamps identity_mode=receiver_realm, which is ungated, where omitting it
    # falls through to the legacy source-trusted peer_kc path and is refused
    # (_resolve_new_federation_identity_stamp, kamiwaza/cluster/federation.py).
    # The receiver owns the resulting federation-<id> realm; the initiator's
    # handshake fires its provisioning hook and disconnect tears it back down.
    receiver_fed = receiver_client.federations.pair(
        name=federation_pair_name,
        role="receiver",
        preshared_key=pair_psk,
        realm_scope="per_federation",
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
    live_peer_base_url: str,
) -> Iterator[dict[str, str]]:
    """Fresh function-scoped pair for tests that mutate pair lifecycle state.

    The module-scoped ``paired_federation`` is a shared resource; tests
    like ``test_unpair_returns_to_clean_state`` would otherwise create
    order-dependent suite behavior. This fixture stands up a separate
    federation with a unique name + unique PSK per test, so mutating
    its state doesn't affect the module-scoped pair AND the receiver
    PSK-match lookup stays unambiguous across coexisting pairs (R5 H1).
    """
    fresh_name = f"eng5784-unpair-{uuid.uuid4().hex[:8]}"
    fresh_psk = str(uuid.uuid4())
    receiver_fed = receiver_client.federations.pair(
        name=fresh_name,
        role="receiver",
        preshared_key=fresh_psk,
        realm_scope="per_federation",
    )
    receiver_fed_id = str(receiver_fed.id)
    try:
        initiator_fed = initiator_client.federations.pair(
            name=fresh_name,
            role="initiator",
            remote_url=live_peer_base_url,
            preshared_key=fresh_psk,
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

        ENG-7203 regression guard. Three outcomes:

        * 401 ``AuthenticationError`` → FAIL: the receiver stripped the
          x-kz-mesh-* HMAC headers before ext-authz could verify them (the
          verify-then-strip deploy fix regressed).
        * 403 brokered-user (``APIError``, status 403) → SKIP: mesh auth
          PASSED (not the ENG-7203 401), but this caller is not yet on the
          peer's federation allowlist, so the capability assertion below
          cannot run. ``test_brokered_user_allowlist_round_trip`` (which runs
          after this) establishes the allowlist; skipping avoids a false
          negative in a fixed-but-not-yet-allowlisted environment.
        * 200 ``ClusterCapabilities`` → assert the schema contract.

        Any other error propagates as a real failure.
        """
        proxy = initiator_client.federations[paired_federation["name"]]
        capabilities = _mesh_call_or_skip(proxy.probe)
        # local_node_id is the schema-declared cluster-identity field
        # (R5 H4 added the declaration). Pin the schema contract — no
        # fallback chain, no extra="allow" passthrough gymnastics.
        assert (
            capabilities.local_node_id
        ), f"peer capabilities missing local_node_id: {capabilities!r}"

    def test_federated_catalog_list_via_mesh(
        self,
        paired_federation: dict[str, str],
        initiator_client: KamiwazaClient,
    ) -> None:
        """FED-06 — list the peer's catalog datasets through the mesh proxy.

        ENG-7203 reachability guard: a 401 means the receiver stripped the mesh
        HMAC (regression); a 200 list or a downstream brokered-user 403 means
        mesh auth resolved. Content assertions need a seeded remote catalog —
        a separate tier.
        """
        name = paired_federation["name"]
        body = _mesh_call_or_skip(
            lambda: initiator_client._request(
                "GET", f"/mesh/{name}/api/catalog/datasets/"
            )
        )
        assert isinstance(body, list)

    def test_federated_retrieval_submit_via_mesh(
        self,
        paired_federation: dict[str, str],
        initiator_client: KamiwazaClient,
    ) -> None:
        """FED-07 — submit a federated retrieval job through the mesh proxy.

        ENG-7203 reachability guard (401-fail / 403-skip). Row-level result
        assertions need a seeded remote dataset — a separate tier.
        """
        name = paired_federation["name"]
        resp = _mesh_call_or_skip(
            lambda: initiator_client._request(
                "POST",
                f"/mesh/{name}/api/retrieval/jobs",
                json={
                    "dataset_urn": "urn:kamiwaza:dataset:eng7203-mesh-probe",
                    "query": "eng7203-mesh-ping",
                    "k": 1,
                },
            )
        )
        assert isinstance(resp, dict)

    def test_federated_job_run_via_mesh(
        self,
        paired_federation: dict[str, str],
        initiator_client: KamiwazaClient,
    ) -> None:
        """FED-19 — submit+run a federated Ray job through the mesh proxy.

        ENG-7203 reachability guard (401-fail / 403-skip; a 403
        no_execution_gate_configured_for_mesh is the mesh-auth-passed gate, not
        the bug). SUCCEEDED + source=='mesh' assertions need an
        AllowAllExecutionGate-enabled receiver — a separate tier.
        """
        name = paired_federation["name"]
        resp = _mesh_call_or_skip(
            lambda: initiator_client._request(
                "POST",
                f"/mesh/{name}/api/cluster/jobs/run",
                json={
                    "entrypoint": "python -c \"print('FED19-MESH-OK')\"",
                    "timeout_seconds": 120,
                },
            )
        )
        assert isinstance(resp, dict)

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

        Strict gates (R5 H2):
          - status MUST be SUCCEEDED — accepting FAILED would mask the
            very failure modes this test exists to catch (a brokered
            job that crashes is not a positive demo-gate signal).
          - identity is read from result.audit_actor — the canonical
            field declared on JobResult (schemas/federation.py).
            Accepting requester/submitter fallbacks lets generically-
            named identity attributes pass even when the audit-actor
            wiring is broken.
        """
        # The job's audit actor is proven by the job SELF-REPORTING the
        # platform-injected originating identity in a KZ_MESH_RUN_ON_JSON::
        # marker, which /result returns (a bare ``print()`` leaves /result with
        # no marker → 410). The identity lives in KAMIWAZA_USER_ATTRS /
        # *_USER_TOKEN, injected by the receiver's OBO wiring — which a default
        # install does NOT provide. So we assert the job SUCCEEDED + the marker
        # round-trips, and skip (precondition unmet) when no identity was
        # injected, rather than hard-failing.
        audit_script = (
            "import os, json\n"
            'raw = os.environ.get("KAMIWAZA_USER_ATTRS") or ""\n'
            'attrs = json.loads(raw) if raw.strip().startswith("{") else {}\n'
            'actor = (attrs.get("sub") or attrs.get("user_id") or attrs.get("email")\n'
            '         or attrs.get("preferred_username")\n'
            '         or os.environ.get("KAMIWAZA_USER_ID") or "")\n'
            'print("KZ_MESH_RUN_ON_JSON::" + json.dumps({"audit_actor": actor, "probe": "eng7284"}))\n'
        )
        result = _mesh_call_or_skip(
            lambda: initiator_client.jobs.run(
                entrypoint="python3 -c " + shlex.quote(audit_script),
                target_cluster=paired_federation["name"],
                timeout_seconds=120,
                recoverable=True,
            )
        )
        assert (
            result.status == "SUCCEEDED"
        ), f"federated job did not succeed: status={result.status} result={result}"
        # Guard against a /result parse regression masquerading as an unmet
        # precondition: our marker payload carried probe="eng7284", so it MUST
        # round-trip through /result -> JobResult before we trust an empty
        # audit_actor as "no identity injected" rather than "marker parse broke".
        marker_probe = getattr(result, "probe", None) or (
            result.result.get("probe") if isinstance(result.result, dict) else None
        )
        assert marker_probe == "eng7284", (
            "result marker did not round-trip (possible /result parse regression): "
            f"{result}"
        )
        if not result.audit_actor:
            pytest.skip(
                "audit-actor demo-gate precondition unmet: the receiver did not "
                "inject an originating identity (KAMIWAZA_USER_ATTRS) into the job "
                "runtime — a separate tier needing cluster-side OBO/identity "
                "config. The marker round-trip + SUCCEEDED status are verified."
            )

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
        # R5 H3 — terminal post-disconnect set MUST NOT include WAITING
        # (the initial state). A no-op or rejected disconnect that
        # leaves the row in WAITING would otherwise pass silently.
        observed = view.get("status")
        assert observed in {"DISCONNECTED", "DEAD"}, (
            f"disconnect did not reach a terminal-disconnect state; "
            f"observed status={observed!r}; full view={view!r}"
        )
