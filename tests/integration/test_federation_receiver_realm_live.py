"""ENG-8213 — Live two-cluster receiver_realm (Alt D / M2) walkthrough.

The live UAT for the ``receiver_realm`` identity mode (design §15). The
counterpart to the mocked backend unit tests, exercised end-to-end against a
real peer cluster:

    pair(realm_scope) → receiver provisions federation-<id> realm → enroll guest →
    mint offline credential (once) → inspect issuer (S6) → list/revoke →
    F5 + not_receiver_realm fail-closed guards → full cross-cluster admit
    (S6+S7+S8: source resolves the per-target credential and the receiver admits
    the guest over the mesh) → unpair (realm torn down)

Topology: the **receiver** (peer cluster) owns the per-federation realm and mints
guest credentials, so all receiver_realm operations run against ``receiver_client``.
The initiator (local cluster) is the source side and only drives the pairing
handshake — which is what triggers the receiver's realm-provisioning hook
(``handle_federation_pairing_request`` → ``_provision_receiver_realm``).

Gated by ``requires_two_clusters`` + ``KAMIWAZA_PEER_BASE_URL`` /
``KAMIWAZA_PEER_API_KEY`` (mirrors test_federation_two_cluster_live.py); auto-
deselected when no peer creds are set, so PRs without a peer rig don't red.

Fleet rig: spark-1 (receiver) ↔ spark-2 (initiator/source). Serve on the
per-host FQDN (spark-N.kale.wemodulate.com) so istio Host-header routing resolves
(memory: reference_spark_fqdn_host_routing).

Tier 2 (full mesh admit) is exercised via §7.5 credential resolution, and that
gate is ARMED: a 401 reds the suite. There is deliberately no xfail marker. The
one that existed until 62b1499 (2026-07-18) was ``strict=False``, which reports
XFAIL and can never red — re-adding it would reproduce ENG-9664 under a new
label. The prose that described it outlived the marker by weeks and is removed
here; doc/behaviour divergence of exactly that kind is what let a total mesh
outage read as expected.

Outcome policy lives in ``mesh_outcome.py`` and is unit-pinned by
``test_mesh_outcome_policy.py``, which runs on every PR without a cluster.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from typing import Any, Iterator

import pytest

from kamiwaza_sdk import KamiwazaClient

from tests.integration import mesh_outcome
from tests.integration.mesh_outcome import MeshPolicy

logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
    pytest.mark.requires_two_clusters,
]

_TIER2_POLICY = MeshPolicy(
    identity_arranged=True,
    admission_is_the_assertion=True,
    context="ENG-8213 Tier-2 receiver_realm mesh admit",
)


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Base64url-decode a JWT's payload segment WITHOUT signature verification
    (inspection only — we assert the receiver-minted issuer/typ, not trust)."""
    parts = token.split(".")
    assert len(parts) >= 2, f"minted credential is not a JWT (got {len(parts)} segs)"
    payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


def _pair_receiver_realm(
    initiator_client: KamiwazaClient,
    receiver_client: KamiwazaClient,
    live_peer_base_url: str,
    name: str,
) -> Iterator[dict[str, str]]:
    """Stand up a receiver_realm federation and tear it down at exit.

    The receiver's ``pair(realm_scope=…)`` creates its side in receiver_realm
    mode; the initiator drives the handshake, which fires the receiver's
    realm-provisioning hook. Yields the both-side ids + name.
    """
    pair_psk = str(uuid.uuid4())
    receiver_fed = receiver_client.federations.pair(
        name=name,
        role="receiver",
        preshared_key=pair_psk,
        realm_scope="per_federation",
    )
    receiver_fed_id = str(receiver_fed.id)
    try:
        # realm_scope is required on the INITIATOR side too. It reads like a
        # receiver-only concern (only the receiver provisions a realm), but the
        # create-time identity stamp is resolved per row: without it the
        # initiator's row falls through to the legacy source-trusted peer_kc
        # mode, which is refused with 400 untrusted_federation_disabled unless
        # ALLOW_UNTRUSTED_FEDERATION is on
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
        # disconnect on both sides (best-effort). On the receiver, disconnect
        # also tears down the provisioned federation-<id> realm (teardown hook).
        for label, client, fed_id in (
            ("initiator", initiator_client, state["initiator_id"]),
            ("receiver", receiver_client, state["receiver_id"]),
        ):
            try:
                client._request("POST", f"/cluster/federations/{fed_id}/disconnect")
            except Exception as exc:  # pragma: no cover - teardown best-effort
                logger.warning("failed to disconnect %s %s: %s", label, fed_id, exc)


@pytest.fixture(scope="module")
def initiator_client(live_kamiwaza_session_client: KamiwazaClient) -> KamiwazaClient:
    return live_kamiwaza_session_client


@pytest.fixture(scope="module")
def receiver_client(live_kamiwaza_peer_client: KamiwazaClient) -> KamiwazaClient:
    return live_kamiwaza_peer_client


@pytest.fixture(scope="module")
def receiver_realm_federation(
    initiator_client: KamiwazaClient,
    receiver_client: KamiwazaClient,
    live_peer_base_url: str,
) -> Iterator[dict[str, str]]:
    name = f"eng8213-rr-{uuid.uuid4().hex[:8]}"
    yield from _pair_receiver_realm(
        initiator_client, receiver_client, live_peer_base_url, name
    )


class TestReceiverRealmWalkthrough:
    """Live receiver_realm round-trip against a real peer cluster."""

    def test_pair_provisions_receiver_realm(
        self,
        receiver_realm_federation: dict[str, str],
        receiver_client: KamiwazaClient,
    ) -> None:
        """After pairing, the receiver's federation record is receiver_realm mode
        with a provisioned federation-<id> realm (the provision hook ran)."""
        rec = receiver_client._request(
            "GET", f"/cluster/federations/{receiver_realm_federation['receiver_id']}"
        )
        assert isinstance(rec, dict)
        assert rec.get("identity_mode") == "receiver_realm"
        fed_realm = rec.get("federation_realm_name")
        assert fed_realm, f"receiver realm not provisioned; record={rec!r}"
        assert fed_realm == f"federation-{receiver_realm_federation['receiver_id']}"

    def test_enroll_guest_mints_offline_credential_once(
        self,
        receiver_realm_federation: dict[str, str],
        receiver_client: KamiwazaClient,
    ) -> None:
        """guests.enroll mints a durable offline credential whose issuer is the
        receiver's federation-<id> realm (validates the S6 issuer-derivation: iss is the
        KC FRONTEND URL of the fed realm, not the internal admin URL)."""
        fed = receiver_client.federations.by_id(
            receiver_realm_federation["receiver_id"]
        )
        external_id = f"uatguest-{uuid.uuid4().hex[:8]}@src"
        guest = fed.guests.enroll(external_id)

        assert guest.offline_token, "offline_token must be returned (once)"
        assert guest.realm == f"federation-{receiver_realm_federation['receiver_id']}"

        claims = _decode_jwt_payload(guest.offline_token)
        iss = claims.get("iss", "")
        assert iss.endswith(f"/realms/{guest.realm}"), (
            f"minted credential issuer {iss!r} is not the fed realm "
            f"{guest.realm!r} — S6 issuer-derivation regressed"
        )
        # Offline (durable) refresh token — typ=Offline distinguishes it from a
        # short-lived Refresh token.
        if "typ" in claims:
            assert claims["typ"] == "Offline"

    def test_enrolled_guest_lists_then_revoke_disables(
        self,
        receiver_realm_federation: dict[str, str],
        receiver_client: KamiwazaClient,
    ) -> None:
        """An enrolled guest surfaces on the federation users list (keyed on its
        federation-realm sub) and revoke disables its allowlist row (FR-79)."""
        fed_id = receiver_realm_federation["receiver_id"]
        fed = receiver_client.federations.by_id(fed_id)
        external_id = f"uatrevoke-{uuid.uuid4().hex[:8]}@src"
        guest = fed.guests.enroll(external_id)
        guest_sub = guest.external_id  # allowlist row is keyed on the realm sub

        users = receiver_client._request("GET", f"/cluster/federations/{fed_id}/users")
        rows = (users.get("items") if isinstance(users, dict) else users) or []
        subs = {str(r.get("external_id")) for r in rows}
        assert guest_sub in subs, f"enrolled guest {guest_sub} not on users list"

        result = fed.guests.revoke(guest_sub)
        assert result.get("success") is True

        after = receiver_client._request("GET", f"/cluster/federations/{fed_id}/users")
        after_rows = (after.get("items") if isinstance(after, dict) else after) or []
        disabled = {str(r.get("external_id")): r.get("disabled_at") for r in after_rows}
        assert disabled.get(guest_sub), "revoked guest row is not disabled"

    def test_enroll_rejects_forbidden_f5_relation(
        self,
        receiver_realm_federation: dict[str, str],
        receiver_client: KamiwazaClient,
    ) -> None:
        """F5 (design §13.4) — a privilege-escalating seed relation (admin) is
        refused fail-closed at enrollment; no credential is minted."""
        from kamiwaza_sdk.exceptions import APIError

        fed = receiver_client.federations.by_id(
            receiver_realm_federation["receiver_id"]
        )
        with pytest.raises(APIError) as exc:
            fed.guests.enroll(
                f"uatf5-{uuid.uuid4().hex[:8]}@src",
                initial_tuples=[
                    {"namespace": "cluster", "object_id": "*", "relation": "admin"}
                ],
            )
        assert getattr(exc.value, "status_code", None) == 400

    def test_guest_credential_admitted_at_receiver_ingress_via_mesh(
        self,
        receiver_realm_federation: dict[str, str],
        receiver_client: KamiwazaClient,
        initiator_client: KamiwazaClient,
        monkeypatch,
    ) -> None:
        """Tier 2 — full cross-cluster admit (S6 + S7 + S8, design §7.5). The
        receiver mints a durable offline guest credential; the source resolves it
        per-target (KAMIWAZA_FEDERATION_CREDENTIAL_<name>) and the source mesh
        proxy forwards it as X-KZ-Federation-Credential; the receiver ADMITS the
        guest identity at ingress.

        ENG-8822 (per-federation mesh PSK resolution) + ENG-8819 (the receiver
        exchanges the HS512 offline token for an RS256 access token at its
        fed-realm and validates that via JWKS) together make the credential
        validate — proven live 2026-07-18. The assertion is that the credential is
        ACCEPTED at ingress: the response is EITHER 200 (guest also authorized for
        the probed resource) OR a downstream per-resource authz 403/404 (F10:
        shared identity ≠ shared authority — a bare guest is admitted but has no
        cluster:viewer grant to probe /cluster_capabilities). What must NOT happen
        is an auth-layer rejection (401, or a peer_jwt_* / mesh-signature 403):
        that would mean the credential itself was refused.
        """
        name = receiver_realm_federation["name"]
        fed = receiver_client.federations.by_id(
            receiver_realm_federation["receiver_id"]
        )
        guest = fed.guests.enroll(f"uatmesh-{uuid.uuid4().hex[:8]}@src")

        # Source resolves the receiver-issued credential for THIS target; the
        # source mesh proxy forwards it as X-KZ-Federation-Credential (§7.5).
        monkeypatch.setenv(
            f"KAMIWAZA_FEDERATION_CREDENTIAL_{name.upper().replace('-', '_')}",
            guest.offline_token,
        )
        # ENG-9664: this gate is ARMED. A 401 here means the credential this
        # test arranged was refused, and that reds the suite. The previous
        # handler classified on getattr(exc, "status_code", None), which a 401
        # never carries (AuthenticationError is a sibling of APIError), so it
        # fell through to a terminal skip and reported green through the total
        # mesh outage of ENG-9663. mesh_outcome keys off the exception TYPE.
        #
        # None means admitted-then-declined downstream: the F10 per-resource
        # authz gate, since a bare guest holds no cluster:viewer grant to probe
        # capabilities. That is the positive Tier-2 signal.
        caps = mesh_outcome.mesh_call(
            lambda: initiator_client.federations[name].probe(), _TIER2_POLICY
        )
        if caps is not None:
            assert caps.system_type  # 200: admitted AND authorized for the resource


class TestReceiverRealmGuardsOnNonReceiverRealmFederation:
    """Guest APIs must be refused on a federation that is NOT receiver_realm."""

    @pytest.fixture
    def peer_kc_federation(
        self,
        initiator_client: KamiwazaClient,
        receiver_client: KamiwazaClient,
        live_peer_base_url: str,
    ) -> Iterator[dict[str, str]]:
        # A plain (peer_kc / shared_idp) pair — no realm_scope, so not
        # receiver_realm. Function-scoped + unique so it doesn't collide.
        from kamiwaza_sdk.exceptions import APIError

        name = f"eng8213-notrr-{uuid.uuid4().hex[:8]}"
        pair_psk = str(uuid.uuid4())
        try:
            receiver_fed = receiver_client.federations.pair(
                name=name, role="receiver", preshared_key=pair_psk
            )
        except APIError as exc:
            # A cluster with ALLOW_UNTRUSTED_FEDERATION=false (the secure default)
            # refuses creating a new peer_kc federation, so we can't stand up a
            # non-receiver_realm federation to assert the guard against. The guard
            # is unit-covered (test_enroll_guest_rejects_non_receiver_realm); skip
            # the live variant rather than red on an env precondition.
            if getattr(exc, "status_code", None) == 400:
                pytest.skip(
                    "cluster refuses peer_kc creation "
                    "(ALLOW_UNTRUSTED_FEDERATION=false); guard is unit-covered"
                )
            raise
        receiver_fed_id = str(receiver_fed.id)
        try:
            initiator_fed = initiator_client.federations.pair(
                name=name,
                role="initiator",
                remote_url=live_peer_base_url,
                preshared_key=pair_psk,
            )
        except Exception:
            try:
                receiver_client._request(
                    "POST", f"/cluster/federations/{receiver_fed_id}/disconnect"
                )
            except Exception:  # pragma: no cover
                pass
            raise
        state = {
            "initiator_id": str(initiator_fed.id),
            "receiver_id": receiver_fed_id,
            "name": name,
        }
        try:
            yield state
        finally:
            for client, fed_id in (
                (initiator_client, state["initiator_id"]),
                (receiver_client, state["receiver_id"]),
            ):
                try:
                    client._request("POST", f"/cluster/federations/{fed_id}/disconnect")
                except Exception:  # pragma: no cover
                    pass

    def test_enroll_refused_on_non_receiver_realm(
        self,
        peer_kc_federation: dict[str, str],
        receiver_client: KamiwazaClient,
    ) -> None:
        from kamiwaza_sdk.exceptions import APIError

        fed = receiver_client.federations.by_id(peer_kc_federation["receiver_id"])
        with pytest.raises(APIError) as exc:
            fed.guests.enroll(f"uatnrr-{uuid.uuid4().hex[:8]}@src")
        assert getattr(exc.value, "status_code", None) == 400
