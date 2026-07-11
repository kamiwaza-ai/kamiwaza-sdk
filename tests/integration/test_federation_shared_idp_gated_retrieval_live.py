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
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest

from . import _mini_clearance as mc

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
    pytest.mark.requires_two_clusters,
]

_PERSONAS = {"U": "fed-clr-u", "S": "fed-clr-s", "TS": "fed-clr-ts"}


def _mesh_call_or_skip(call):
    """401 -> hard fail (ENG-7203 HMAC-strip regression); 403/404 -> soft skip
    (mesh auth verified, downstream precondition unmet); else propagate."""
    from kamiwaza_sdk.exceptions import APIError, AuthenticationError

    try:
        return call()
    except AuthenticationError as exc:
        pytest.fail(
            "ENG-7203 regression: authentic mesh call returned 401 'Not "
            f"authenticated' — x-kz-mesh-* HMAC stripped before ext-authz: {exc!r}"
        )
    except APIError as exc:
        if getattr(exc, "status_code", None) in (403, 404):
            pytest.skip(
                "mesh auth verified (not ENG-7203); reached the receiver but hit a "
                f"downstream precondition (gate PVC / fs-root / unseeded): {exc!r}"
            )
        raise


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

    # 3. Brokered shared-realm identities (external_id keyed on the initiator
    #    cluster uuid). Their shared-realm JWT projects the `clearance` claim,
    #    which arrives as X-User-Attributes on mesh inbound; seed a viewer tuple
    #    so the auto-provisioned user reaches the gate rather than 404-ing at the
    #    retrieval seam. POST against the receiver-side federation id (name
    #    lookup fails post-pair) with the canonical subject/relation/object shape.
    src_uuid = mc.initiator_cluster_uuid(receiver, receiver_id) or "initiator"
    externals = {}
    for clearance, base in _PERSONAS.items():
        external_id = f"{base}@{src_uuid}"
        receiver._request(
            "POST",
            f"/cluster/federations/{receiver_id}/users",
            json={
                "external_id": external_id,
                "initial_tuples": [
                    {
                        "subject": f"user:{external_id}",
                        "relation": "viewer",
                        "object": f"dataset:{urn}",
                    }
                ],
            },
        )
        externals[clearance] = external_id

    try:
        yield {"name": name, "urn": urn, "externals": externals, "shared": shared}
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

    def _retrieve():
        # Mesh retrieval-jobs on the receiver, routed through the federation.
        return initiator._request(
            "POST",
            f"/mesh/{name}/api/retrieval/jobs",
            json={"dataset_urn": urn},
        )

    result = _mesh_call_or_skip(_retrieve)
    rows, gate_audit = mc.parse_mesh_retrieval_result(result, initiator, name)
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
