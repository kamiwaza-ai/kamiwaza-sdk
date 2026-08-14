"""ENG-8325 Layer 3 — two-cluster shared_idp known-answer gated retrieval.

The sibling that ``test_federation_identity_gate_live.py`` forward-declares: the
data-plane round-trip on top of the single-cluster create-gate + posture surface.
A federated (shared_idp) caller on the initiator (spark-1) retrieves a
gate-bound dataset on the receiver (spark-2) over the mesh and receives ONLY the
rows its clearance permits — asserted by exact post-gate counts, not reachability.

Layers:
  * gate-as-dataset-feature (single cluster) is Layer 2
    (test_mini_clearance_gate_retrieval_live.py);
  * gate LOGIC is Layer 1 (kamiwaza tests/unit/.../test_mini_clearance_gate.py).

requires_two_clusters — auto-deselected without --live-peer-base-url. Data-plane
assertions are tiered behind _mesh_call_or_skip: an authentic-persona 401 is a
hard ENG-7203 regression; a 403/404 (gate-package PVC, filesystem root, or mesh
data-plane precondition unmet) is a soft skip. Beyond the two-cluster rig, the
operator must have provisioned on the RECEIVER: the WS-M5 gate-packages PVC + a
served 1.1.0 wheel (M5_TEST_WHEEL_DIR/M5_TEST_INDEX_URL), the fixture file under
RETRIEVAL_FILESYSTEM_ALLOWED_ROOTS (MINI_CLEARANCE_DATASET_PATH), and a shared
realm that projects the ``clearance`` claim into brokered persona JWTs
(SHARED_ISSUER_URL[/SHARED_JWKS_URL/SHARED_CA_PEM]); else the round-trip skips.

Persona auth (SHARED_REALM_CLIENT_ID / FED_PERSONA_PASSWORD): each persona mints
a token via ROPC against the shared realm's token endpoint (a direct-access-
grants client), so the receiver's shared_idp peer-JWT validation accepts it
(its kid is in the shared JWKS — an admin/local-realm token's kid is not) and
the gate sees the realm-projected ``clearance`` claim. Shared-realm setup shape
(on the shared realm, e.g. spark-1 ``federated``): a ROPC client (public,
directAccessGrantsEnabled); persona users ``fed-clr-{u,s,ts}`` with a
``clearance`` user-attribute (needs the realm user-profile's
``unmanagedAttributePolicy=ENABLED``) + email/first/last so ROPC isn't blocked
"Account is not fully set up"; and an ``oidc-usermodel-attribute-mapper``
projecting ``clearance`` as a top-level access-token claim.

LIVE STATUS (2026-07-11): the auth boundary is CROSSED — with the persona
shared-realm token the receiver's peer-JWT validation PASSES (the historic
``peer_jwt_validation_failed: kid not present`` is gone) and the mesh reaches
the data plane. The 3 persona assertions still tier to SKIP on a downstream
403 ``Forbidden``: the mesh retrieval's dataset-view authz
(catalog.identity.resolve_requester_urn → a DataHub ``urn:li:corpuser`` URN) is
NOT satisfied by the federation allowlist's ``initial_tuples`` viewer grant
(a ``user:{{user_id}}`` namespaced ReBAC tuple). Reconciling those two subject
namespaces for a brokered mesh caller is the remaining layer to flip the SKIPs
to exact-count assertions.

LIVE STATUS (2026-08-07) — supersedes the 403 prediction above (ENG-9813). The
observed failure is a **401 one layer EARLIER**, at the receiver's
identity-header verification, so the run never reached the dataset-view authz
the note above describes. Root cause: the mesh identity producer emitted
``X-User-Attributes`` without its companion ``X-User-Attributes-Hash``. That
header travels unsigned and only the digest is a ForwardAuth HMAC payload
field, so the origin read the absent digest as "there must be no attributes
either" and rejected before any gate ran (kamiwaza ``49b03ecd7``).

These personas are precisely what exposed it: their realm-projected
``clearance`` claim is what makes the attribute header non-empty. Modes
carrying no attributes — ``receiver_realm``, whose F10 role-stripped guests
have none — passed throughout, which is why the defect read as
shared_idp-specific when it was not.

The 403 analysis above is NOT withdrawn; it is simply downstream and was never
reached. Expect it to become the live symptom again once the fix is deployed,
at which point the subject-namespace reconciliation remains the real work.
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest

from tests.integration import mesh_outcome
from tests.integration.mesh_outcome import MeshPolicy

from . import _mini_clearance as mc

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
    pytest.mark.requires_two_clusters,
]

_PERSONAS = {"U": "fed-clr-u", "S": "fed-clr-s", "TS": "fed-clr-ts"}


_SHARED_IDP_POLICY = MeshPolicy(
    identity_arranged=True,
    admission_is_the_assertion=False,
    context="ENG-8325 shared_idp gated retrieval",
)


def _mesh_call_or_skip(call):
    """401 -> hard fail (ENG-7203 HMAC-strip regression); 403/404 -> soft skip
    (mesh auth verified, downstream precondition unmet); else propagate.

    ENG-9664: delegates to the shared ``mesh_outcome`` classifier. This file
    already failed on 401 and that is preserved exactly. The one change is that
    an auth-layer-marked 403 — the receiver refusing the credential rather than
    a downstream precondition — now fails instead of skipping.
    """
    return mesh_outcome.mesh_call(call, _SHARED_IDP_POLICY)


def _shared_realm() -> dict[str, str]:
    issuer = os.getenv("SHARED_ISSUER_URL", "").strip()
    if not issuer:
        pytest.skip(
            "SHARED_ISSUER_URL not set — shared_idp pairing needs a shared realm "
            "(and it must project the `clearance` claim into brokered JWTs)"
        )
    cfg = {"shared_issuer_url": issuer}
    for env, key in (("SHARED_JWKS_URL", "shared_jwks_url"), ("SHARED_CA_PEM", "shared_ca_pem")):
        val = os.getenv(env, "").strip()
        if val:
            cfg[key] = val
    return cfg


def _persona_auth() -> dict:
    """Shared-realm ROPC config for the personas (soft-skip when absent).

    The mesh data-plane assertions need each persona to present a token minted
    by the SHARED realm (a direct-access-grants client + the persona password),
    so the receiver's shared_idp peer-JWT validation accepts it (kid is in the
    shared JWKS) and the gate sees the realm-projected `clearance` claim.
    """
    client_id = os.getenv("SHARED_REALM_CLIENT_ID", "").strip()
    password = os.getenv("FED_PERSONA_PASSWORD", "").strip()
    if not client_id or not password:
        pytest.skip(
            "SHARED_REALM_CLIENT_ID / FED_PERSONA_PASSWORD not set — the personas "
            "must present a shared-realm ROPC token for the mesh data-plane "
            "assertions (a direct-access-grants client + persona password on the "
            "shared realm that projects the `clearance` claim)"
        )
    verify = os.getenv("KAMIWAZA_VERIFY_SSL", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    return {
        "client_id": client_id,
        "client_secret": os.getenv("SHARED_REALM_CLIENT_SECRET", "").strip() or None,
        "password": password,
        "verify": verify,
    }


def _fed_name() -> str:
    return f"eng8325-sharedidp-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def _receiver_prereqs() -> tuple[str, str, str]:
    wi = mc.wheel_and_index()
    if wi is None:
        pytest.skip("gate-packages wheel/index not configured on the receiver")
    dataset_path = os.getenv("MINI_CLEARANCE_DATASET_PATH", "").strip()
    if not dataset_path:
        pytest.skip("MINI_CLEARANCE_DATASET_PATH not set (receiver fixture file)")
    return wi[0], wi[1], dataset_path


@pytest.fixture(scope="module")
def shared_idp_gated_pair(
    _receiver_prereqs,
    live_kamiwaza_session_client,
    live_kamiwaza_peer_client,
    live_peer_base_url,
) -> Iterator[dict]:
    """Pair spark-1<->spark-2 in shared_idp mode; seed the receiver's gated
    dataset + brokered U/S/TS identities. Yields the wiring; tears down at exit."""
    wheel_dir, index_url, dataset_path = _receiver_prereqs
    initiator = live_kamiwaza_session_client
    receiver = live_kamiwaza_peer_client
    shared = _shared_realm()
    name = _fed_name()
    psk = uuid.uuid4().hex

    # 1. shared_idp pairing (receiver-controlled; not gated by ALLOW_UNTRUSTED).
    #    Capture the receiver-side federation id — brokered-user writes and the
    #    initiator-cluster-uuid lookup both key on it, since /pair overwrites the
    #    receiver's remote_cluster_name with the initiator's cluster name.
    recv_fed = receiver.federations.pair(
        name=name, role="receiver", preshared_key=psk, **shared
    )
    receiver_id = str(recv_fed.id)
    initiator.federations.pair(
        name=name, role="initiator", remote_url=live_peer_base_url, preshared_key=psk, **shared
    )

    # 2. Seed the receiver's gated dataset.
    mc.declare_clearance_attribute(receiver)
    mc.install_gate_package(receiver, wheel_dir, index_url)
    urn = mc.create_file_dataset(receiver, f"mini-clearance-{name}", dataset_path)

    # 3. Brokered shared-realm identities. Each persona authenticates to the
    #    SHARED realm (ROPC) for a token whose `sub` is the federated user id and
    #    whose `clearance` claim rides X-User-Attributes to the gate. The receiver
    #    keys the brokered user on `<sub>@<initiator-cluster-uuid>` (the
    #    X-KZ-Mesh-User-Id the initiator forwards), so seed the viewer tuple on the
    #    token `sub`, not the username. POST against the receiver-side federation
    #    id (name lookup fails post-pair) with the canonical tuple shape.
    src_uuid = mc.initiator_cluster_uuid(receiver, receiver_id) or "initiator"
    auth = _persona_auth()
    issuer = shared["shared_issuer_url"]
    personas: dict = {}
    for clearance, base in _PERSONAS.items():
        token = mc.shared_realm_token(
            issuer,
            auth["client_id"],
            base,
            auth["password"],
            client_secret=auth["client_secret"],
            verify=auth["verify"],
        )
        sub = mc.jwt_sub(token) or base
        external_id = f"{sub}@{src_uuid}"
        receiver._request(
            "POST",
            f"/cluster/federations/{receiver_id}/users",
            json={
                "external_id": external_id,
                "initial_tuples": [
                    # {{user_id}} renders to the local provisioned user id at
                    # ingress (brokering._render_placeholder), so the viewer grant
                    # lands on the subject the retrieval authz actually evaluates.
                    {
                        "subject": "user:{{user_id}}",
                        "relation": "viewer",
                        "object": f"dataset:{urn}",
                    }
                ],
            },
        )
        personas[clearance] = {"token": token, "external_id": external_id, "sub": sub}

    try:
        yield {
            "name": name,
            "urn": urn,
            "personas": personas,
            "shared": shared,
            "verify": auth["verify"],
        }
    finally:
        for who in (initiator, receiver):
            try:
                who.federations[name].disconnect()
            except Exception:  # noqa: BLE001
                pass
        try:
            receiver.datasets.delete(urn)
        except Exception:  # noqa: BLE001
            pass
        try:
            receiver.gates.packages.uninstall("acme-gates")
        except Exception:  # noqa: BLE001
            pass


@pytest.mark.parametrize("clearance", ["U", "S", "TS"])
def test_federated_persona_sees_exact_post_gate_counts(
    clearance, shared_idp_gated_pair, live_kamiwaza_session_client
) -> None:
    """A shared_idp persona mesh-retrieves exactly its allowed rows — known answer."""
    wiring = shared_idp_gated_pair
    initiator = live_kamiwaza_session_client
    name, urn = wiring["name"], wiring["urn"]
    # Present the persona's SHARED-realm token (carries `clearance`) on the mesh
    # call — not the admin/local-realm client, whose kid isn't in the shared JWKS.
    token = wiring["personas"][clearance]["token"]
    persona = mc.raw_token_client(initiator.base_url, token, verify=wiring["verify"])

    def _retrieve():
        # Create the retrieval job over the mesh AND drain its gated SSE stream
        # over the mesh — the results + gate_audit footer arrive on the stream,
        # not the async create response. A 403/404 on either leg soft-skips.
        return mc.mesh_retrieve_through_gate(
            persona, initiator.base_url, token, name, urn, verify=wiring["verify"]
        )

    rows, gate_audit = _mesh_call_or_skip(_retrieve)
    mc.assert_persona_result(clearance, rows, gate_audit)


def test_fabricated_non_shared_token_rejected_before_gate(
    shared_idp_gated_pair, live_peer_base_url
) -> None:
    """A token NOT signed by the shared realm is rejected at the receiver's
    ext-authz JWKS check (401) and never reaches the gate — the shared_idp trust
    boundary. Here a 401 for the fabricated token is the PASS."""
    from kamiwaza_sdk.exceptions import APIError, AuthenticationError

    verify = os.getenv("KAMIWAZA_VERIFY_SSL", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    forged = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJmb3JnZWQifQ.not-a-real-signature"
    client = mc.raw_token_client(live_peer_base_url, forged, verify=verify)
    with pytest.raises((AuthenticationError, APIError)) as exc:
        client._request("GET", "/cluster/diagnose")
    status = getattr(exc.value, "status_code", None)
    assert isinstance(exc.value, AuthenticationError) or status in (401, 403), (
        f"fabricated non-shared token was not rejected fail-closed: {exc.value!r}"
    )
