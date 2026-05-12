"""T5.5 / §4.2.11 — kamiwaza.subjects module tests.

Customer-facing surface for AuthzSubjects per design §4.2.11:

    kz.subjects.upsert(username, attributes=..., password=...) -> Subject
    kz.subjects.get(username)                                  -> Subject
    kz.subjects.delete(username, cascade_grants=True)          -> None
    kz.subjects.grants(username).create(...)                   -> Grant
    kz.subjects.grants(username).list()                        -> list[Grant]
    kz.subjects.grants(username).delete(...)                   -> None

Server-side correlates (mounted at /api/authz):
    PUT    /api/authz/subjects/{id_or_username}
    GET    /api/authz/subjects/{id_or_username}
    DELETE /api/authz/subjects/{id_or_username}?cascade=grants
    POST   /api/authz/subjects/{id_or_username}/grants
    GET    /api/authz/subjects/{id_or_username}/grants
    DELETE /api/authz/subjects/{id_or_username}/grants

v0.3.5 OQ-11: PUT-only on the upsert path (no POST).
"""

from __future__ import annotations

import json
from typing import Any

import pytest


def test_kamiwaza_exposes_subjects_attribute() -> None:
    """client.subjects is the entry point for subject lifecycle."""
    from kamiwaza.client import Kamiwaza

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    assert client.subjects is not None


def test_subjects_is_lazy_loaded() -> None:
    """Lazy-load per .ai/rules/sdk-patterns.md."""
    from kamiwaza.client import Kamiwaza

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    a = client.subjects
    b = client.subjects
    assert a is b


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_upsert_puts_to_server_with_attributes(httpx_mock: Any) -> None:
    """kz.subjects.upsert puts to /api/authz/subjects/{username} (v0.3.5 OQ-11)."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.models import Subject

    httpx_mock.add_response(
        method="PUT",
        url="https://kamiwaza.test/api/authz/subjects/alice",
        status_code=200,
        json={
            "id": "kc-alice-uuid",
            "username": "alice",
            "attributes": {"clearance": "S", "country": "GBR"},
            "grants": [],
            "created_at": "2026-05-12T00:00:00+00:00",
            "updated_at": "2026-05-12T00:00:00+00:00",
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    result = client.subjects.upsert(
        "alice", attributes={"clearance": "S", "country": "GBR"}
    )

    assert isinstance(result, Subject)
    assert result.id == "kc-alice-uuid"
    assert result.username == "alice"
    assert result.attributes["clearance"] == "S"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_upsert_forwards_password_in_body(httpx_mock: Any) -> None:
    """SDK passes password through as the body's `password` field."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="PUT",
        url="https://kamiwaza.test/api/authz/subjects/bob",
        status_code=200,
        json={
            "id": "kc-bob-uuid",
            "username": "bob",
            "attributes": {"clearance": "U"},
            "grants": [],
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    client.subjects.upsert("bob", attributes={"clearance": "U"}, password="initial-pw")

    request = httpx_mock.get_requests(method="PUT")[0]
    body = json.loads(request.content)
    assert body == {
        "attributes": {"clearance": "U"},
        "password": "initial-pw",
    }


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_upsert_omits_password_field_when_none(httpx_mock: Any) -> None:
    """When password=None, the field is omitted from the body — server
    treats no-password as 'don't touch credentials' (T3.2 semantics)."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="PUT",
        url="https://kamiwaza.test/api/authz/subjects/carol",
        status_code=200,
        json={
            "id": "kc-carol-uuid",
            "username": "carol",
            "attributes": {"clearance": "C"},
            "grants": [],
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    client.subjects.upsert("carol", attributes={"clearance": "C"})

    request = httpx_mock.get_requests(method="PUT")[0]
    body = json.loads(request.content)
    assert body == {"attributes": {"clearance": "C"}}


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_get_returns_subject(httpx_mock: Any) -> None:
    """kz.subjects.get → GET /api/authz/subjects/{username} → Subject."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.models import Subject

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/authz/subjects/dan",
        status_code=200,
        json={
            "id": "kc-dan-uuid",
            "username": "dan",
            "attributes": {"clearance": "TS"},
            "grants": [],
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    subject = client.subjects.get("dan")

    assert isinstance(subject, Subject)
    assert subject.id == "kc-dan-uuid"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_get_raises_on_404(httpx_mock: Any) -> None:
    """404 subject_not_found surfaces as KamiwazaError per T5.10 mapping."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.exceptions import KamiwazaError

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/authz/subjects/ghost",
        status_code=404,
        json={"detail": {"reason": "subject_not_found", "id_or_username": "ghost"}},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    with pytest.raises(KamiwazaError):
        client.subjects.get("ghost")


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_delete_sends_delete_without_cascade_by_default(httpx_mock: Any) -> None:
    """kz.subjects.delete defaults to no cascade — grants stay put."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="DELETE",
        url="https://kamiwaza.test/api/authz/subjects/eve",
        status_code=200,
        json={"deleted": True, "username": "eve"},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    client.subjects.delete("eve")

    request = httpx_mock.get_requests(method="DELETE")[0]
    # No cascade query param when cascade_grants is False.
    assert "cascade" not in request.url.query.decode()


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_delete_with_cascade_grants_passes_query_param(httpx_mock: Any) -> None:
    """cascade_grants=True adds ?cascade=grants per T3.5 server contract."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="DELETE",
        url="https://kamiwaza.test/api/authz/subjects/frank?cascade=grants",
        status_code=200,
        json={"deleted": True, "username": "frank"},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    client.subjects.delete("frank", cascade_grants=True)


# ─── Grants sub-resource ──────────────────────────────────────────────────


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_grants_create_posts_to_subject_scoped_endpoint(httpx_mock: Any) -> None:
    """kz.subjects.grants('alice').create(...) → POST /api/authz/subjects/alice/grants."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.models import Grant

    httpx_mock.add_response(
        method="POST",
        url="https://kamiwaza.test/api/authz/subjects/alice/grants",
        status_code=201,
        json={
            "object_namespace": "cluster",
            "object_id": "cluster-uuid-1",
            "relation": "viewer",
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    grant = client.subjects.grants("alice").create(
        object_namespace="cluster",
        object_id="cluster-uuid-1",
        relation="viewer",
    )

    assert isinstance(grant, Grant)
    assert grant.relation == "viewer"

    request = httpx_mock.get_requests(method="POST")[0]
    body = json.loads(request.content)
    assert body == {
        "object_namespace": "cluster",
        "object_id": "cluster-uuid-1",
        "relation": "viewer",
    }


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_grants_list_returns_typed_grants(httpx_mock: Any) -> None:
    """kz.subjects.grants('alice').list() returns list[Grant]."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.models import Grant

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/authz/subjects/alice/grants",
        status_code=200,
        json=[
            {
                "object_namespace": "cluster",
                "object_id": "c1",
                "relation": "viewer",
            },
            {
                "object_namespace": "dataset",
                "object_id": "d1",
                "relation": "owner",
            },
        ],
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    grants = client.subjects.grants("alice").list()

    assert len(grants) == 2
    assert all(isinstance(g, Grant) for g in grants)
    assert {g.relation for g in grants} == {"viewer", "owner"}


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_grants_delete_sends_tuple_key_in_body(httpx_mock: Any) -> None:
    """kz.subjects.grants('alice').delete(...) → DELETE with tuple-key body
    (relation values can contain characters that don't path-encode cleanly,
    so the server takes the tuple in the body)."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="DELETE",
        url="https://kamiwaza.test/api/authz/subjects/alice/grants",
        status_code=200,
        json={"deleted": True},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    client.subjects.grants("alice").delete(
        object_namespace="cluster",
        object_id="c1",
        relation="viewer",
    )

    request = httpx_mock.get_requests(method="DELETE")[0]
    body = json.loads(request.content)
    assert body == {
        "object_namespace": "cluster",
        "object_id": "c1",
        "relation": "viewer",
    }
