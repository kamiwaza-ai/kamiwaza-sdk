"""T5.3 / ENG-4679 — kamiwaza.federations module tests.

Verifies the SDK federations API surface per design §4.2.11:
    - kz.federations.pair(name, role, remote_url, ...) → Federation
    - kz.federations.list() → list[Federation]
    - kz.federations.get(name) → Federation
    - kz.federations[name] indexed access → FederationProxy
    - kz.federations[name].users.add(external_id, ...) → BrokeredUser
    - kz.federations[name].users.list() → list[BrokeredUser]
    - kz.federations[name].users.revoke(external_id) → None

Skeleton scope: pair + indexed access + users.add (T4.16-skeleton drives
through these). list/get/revoke land in subsequent T5.x cycles.
"""

from __future__ import annotations

from typing import Any

import pytest


def test_kamiwaza_exposes_federations_attribute() -> None:
    """``client.federations`` is the entry point for federation operations."""
    from kamiwaza.client import Kamiwaza

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    assert client.federations is not None


def test_federations_is_lazy_loaded() -> None:
    """Per .ai/rules/sdk-patterns.md, services are lazy-loaded so two
    accesses return the same instance and unused services don't take up
    memory."""
    from kamiwaza.client import Kamiwaza

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    a = client.federations
    b = client.federations
    assert a is b


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_pair_posts_to_federations_endpoint(httpx_mock: Any) -> None:
    """``kz.federations.pair("ORION", role="initiator", ...)`` POSTs to
    ``/api/cluster/federations`` to create the federation record, then
    POSTs to ``/api/cluster/federations/{id}/pair`` to drive the
    handshake. T5.3 ships the typed wrapper; the server-side endpoints
    are owned by T1.x and the existing federation API."""
    from kamiwaza.client import Kamiwaza

    federation_id = "fed-orion-123"

    httpx_mock.add_response(
        method="POST",
        url="https://kamiwaza.test/api/cluster/federations",
        status_code=201,
        json={
            "id": federation_id,
            "status": "PAIRING",
            "remote_cluster_name": "ORION",
            "remote_ips": [{"ip": "10.0.0.99", "primary": True}],
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=f"https://kamiwaza.test/api/cluster/federations/{federation_id}/pair",
        status_code=200,
        json={
            "id": federation_id,
            "status": "PAIRED",
            "remote_cluster_name": "ORION",
            "remote_ips": [{"ip": "10.0.0.99", "primary": True}],
            "callback_hostname": "edge.lyra.example.com",
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    fed = client.federations.pair(
        name="ORION",
        role="initiator",
        remote_url="https://orion.example.com",
        remote_admin_token="orion-pat",
    )

    assert fed.id == federation_id
    assert fed.status == "PAIRED"
    assert fed.callback_hostname == "edge.lyra.example.com"

    # Verify both server calls were made in the right order.
    requests = httpx_mock.get_requests()
    assert len(requests) == 2
    assert requests[0].url.path == "/api/cluster/federations"
    assert requests[1].url.path == (f"/api/cluster/federations/{federation_id}/pair")


def test_indexed_access_returns_federation_proxy() -> None:
    """``kz.federations["ORION"]`` returns a FederationProxy bound to
    the federation name. The proxy lazily resolves the federation record
    when needed (e.g. on .users.add) so simple index access doesn't
    cost a round-trip."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.federations import FederationProxy

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    proxy = client.federations["ORION"]

    assert isinstance(proxy, FederationProxy)
    assert proxy.name == "ORION"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_users_add_posts_with_external_id(httpx_mock: Any) -> None:
    """``kz.federations[name].users.add(external_id="cdr-baker", ...)``
    POSTs to ``/api/cluster/federations/{id}/users``. Resolves the
    federation by name first (GET /federations) to get the id, then
    posts the user record. Returns BrokeredUser."""
    from kamiwaza.client import Kamiwaza

    federation_id = "fed-orion-123"

    # Name → id resolution.
    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/federations",
        status_code=200,
        json={
            "items": [
                {
                    "id": federation_id,
                    "status": "PAIRED",
                    "remote_cluster_name": "ORION",
                }
            ]
        },
    )
    # User creation.
    httpx_mock.add_response(
        method="POST",
        url=(f"https://kamiwaza.test/api/cluster/federations/{federation_id}/users"),
        status_code=201,
        json={
            "federation_id": federation_id,
            "external_id": "cdr-baker@lyra-cluster-uuid",
            "auto_provisioned": False,
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    user = client.federations["ORION"].users.add(
        external_id="cdr-baker@lyra-cluster-uuid",
        initial_tuples=[
            {
                "subject": "user:cdr-baker@lyra-cluster-uuid",
                "relation": "viewer",
                "object": "cluster:ORION",
            }
        ],
    )

    assert user.external_id == "cdr-baker@lyra-cluster-uuid"
    assert user.federation_id == federation_id


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_users_add_raises_brokered_user_not_allowlisted_on_403(
    httpx_mock: Any,
) -> None:
    """When the server rejects a users.add with the typed reason, the
    SDK surfaces it as BrokeredUserNotAllowlistedError per T5.10 dispatch
    table — the federations module doesn't reinvent error handling."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.exceptions import BrokeredUserNotAllowlistedError

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/federations",
        status_code=200,
        json={
            "items": [
                {
                    "id": "fed-orion",
                    "status": "PAIRED",
                    "remote_cluster_name": "ORION",
                }
            ]
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=("https://kamiwaza.test/api/cluster/federations/fed-orion/users"),
        status_code=403,
        json={
            "detail": {
                "reason": "brokered_user_not_allowlisted",
                "external_id": "evil-actor",
            }
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")

    with pytest.raises(BrokeredUserNotAllowlistedError):
        client.federations["ORION"].users.add(external_id="evil-actor")


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_users_add_passes_initial_tuples_in_request_body(httpx_mock: Any) -> None:
    """Existing tests assert on the response shape only. This one nails
    down the request-body contract: ``initial_tuples`` from the SDK
    call must appear in the POST body so the server-side allowlist
    seeding sees the same shape the customer wrote."""
    from kamiwaza.client import Kamiwaza

    federation_id = "fed-orion-123"
    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/federations",
        status_code=200,
        json={
            "items": [
                {
                    "id": federation_id,
                    "status": "PAIRED",
                    "remote_cluster_name": "ORION",
                }
            ]
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=f"https://kamiwaza.test/api/cluster/federations/{federation_id}/users",
        status_code=201,
        json={
            "federation_id": federation_id,
            "external_id": "cdr-baker@lyra-cluster-uuid",
            "auto_provisioned": False,
        },
    )

    initial_tuples = [
        {
            "subject": "user:cdr-baker@lyra-cluster-uuid",
            "relation": "viewer",
            "object": "cluster:ORION",
        },
        {
            "subject": "user:cdr-baker@lyra-cluster-uuid",
            "relation": "member",
            "object": "group:analysts",
        },
    ]

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    client.federations["ORION"].users.add(
        external_id="cdr-baker@lyra-cluster-uuid",
        initial_tuples=initial_tuples,
    )

    import json as _json

    requests = httpx_mock.get_requests()
    post_request = next(r for r in requests if r.method == "POST")
    body = _json.loads(post_request.read())
    assert body["external_id"] == "cdr-baker@lyra-cluster-uuid"
    assert body["initial_tuples"] == initial_tuples


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_indexed_access_users_add_raises_when_name_not_found(
    httpx_mock: Any,
) -> None:
    """``kz.federations["NONEXISTENT"].users.add(...)`` triggers a name→id
    resolution that walks the federations list. When no matching name is
    found, the SDK surfaces a ``KamiwazaError`` with a message naming the
    missing federation rather than silently posting to a bogus path."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.exceptions import KamiwazaError

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/federations",
        status_code=200,
        json={
            "items": [
                {
                    "id": "fed-orion",
                    "status": "PAIRED",
                    "remote_cluster_name": "ORION",
                }
            ]
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    with pytest.raises(KamiwazaError) as exc_info:
        client.federations["NONEXISTENT"].users.add(external_id="someone")
    assert "NONEXISTENT" in str(exc_info.value)
