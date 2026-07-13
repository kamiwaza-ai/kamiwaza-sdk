"""ENG-8213 — live known-answer proof of the federation identity gate + posture.

Single-cluster live checks (no peer required): the receiving cluster REFUSES a
new source-trusted ``peer_kc`` federation when ``ALLOW_UNTRUSTED_FEDERATION`` is
off (the secure default), and ADMITS a receiver-controlled ``shared_idp``
federation, surfacing its trust posture. Known-answer: exact reason string and
exact posture fields, not a reachability smoke.

The two-cluster known-answer gated-retrieval round-trip (shared_idp mesh + a
seeded dataset with exact gated/ungated counts) is the sibling
``requires_two_clusters`` test; these single-cluster checks validate the
create-time gate + posture surface that it builds on.

Verified live on spark-1 (2026-07-08).
"""

from __future__ import annotations

import uuid

import pytest

from kamiwaza_sdk.exceptions import APIError

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def _cleanup(client, name_prefix: str) -> None:
    """Delete any federation rows this test created (best-effort)."""
    for fed in client._request("GET", "/cluster/federations") or []:
        if str(fed.get("remote_cluster_name", "")).startswith(name_prefix):
            try:
                client._request("DELETE", f"/cluster/federations/{fed['id']}")
            except APIError:
                pass


def test_new_peer_kc_federation_refused_when_untrusted_disabled(
    live_kamiwaza_client,
) -> None:
    """A new peer_kc federation is refused with 400 untrusted_federation_disabled
    when ALLOW_UNTRUSTED_FEDERATION is off (default). ENG-8322 / design §13.5."""
    name = f"uat-peerkc-{uuid.uuid4().hex[:8]}"
    try:
        with pytest.raises(APIError) as excinfo:
            live_kamiwaza_client._request(
                "POST",
                "/cluster/federations",
                json={
                    "remote_cluster_name": name,
                    "role": "receiver",
                    "preshared_key": str(uuid.uuid4()),
                },
            )
        msg = str(excinfo.value)
        assert "400" in msg
        assert "untrusted_federation_disabled" in msg
    finally:
        _cleanup(live_kamiwaza_client, name)


def test_shared_idp_federation_admitted_with_posture(live_kamiwaza_client) -> None:
    """A shared_idp federation is admitted (not gated) and surfaces its
    receiver-controlled trust posture. ENG-8324 / design §13.8."""
    name = f"uat-sharedidp-{uuid.uuid4().hex[:8]}"
    try:
        created = live_kamiwaza_client._request(
            "POST",
            "/cluster/federations",
            json={
                "remote_cluster_name": name,
                "role": "receiver",
                "preshared_key": str(uuid.uuid4()),
                "shared_issuer_url": "https://shared.example/realms/federated",
            },
        )
        assert created["identity_mode"] == "shared_idp"
        assert created["trust_posture"] == "receiver_controlled_shared"
        assert created["grandfathered"] is False
    finally:
        _cleanup(live_kamiwaza_client, name)
