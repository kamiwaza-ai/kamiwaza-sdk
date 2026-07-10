"""T5.5 / §4.2.11 — SubjectsAPI on the canonical kamiwaza_sdk surface.

WS-M3.2 test migration (T7.15 / ENG-5049). Customer-facing AuthzSubjects
surface per design §4.2.11:

    kz.subjects.upsert(username, attributes=..., password=...) -> Subject
    kz.subjects.get(username)                                  -> Subject
    kz.subjects.delete(username, cascade_grants=True)          -> None
    kz.subjects.grants(username).create(...)                   -> Grant
    kz.subjects.grants(username).list()                        -> list[Grant]
    kz.subjects.grants(username).delete(...)                   -> None

v0.3.5 OQ-11: PUT-only on upsert. M3 PR-feedback M3 (test gap): a real
special-character username is exercised in
``test_upsert_url_encodes_special_char_username`` to actually catch a
regression in ``_encode_username``.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest


def test_upsert_puts_to_server_with_attributes(mock_client) -> None:
    """kz.subjects.upsert puts to /authz/subjects/{username} (OQ-11)."""
    from kamiwaza_sdk.schemas.federation import Subject
    from kamiwaza_sdk.services.subjects import SubjectsAPI

    mock_client.expect(
        "PUT",
        "/authz/subjects/alice",
        {
            "id": "kc-alice-uuid",
            "username": "alice",
            "attributes": {"clearance": "S", "country": "GBR"},
            "grants": [],
            "created_at": "2026-05-12T00:00:00+00:00",
            "updated_at": "2026-05-12T00:00:00+00:00",
        },
    )

    result = SubjectsAPI(client=mock_client).upsert(
        "alice", attributes={"clearance": "S", "country": "GBR"}
    )

    assert isinstance(result, Subject)
    assert result.id == "kc-alice-uuid"
    assert result.username == "alice"
    assert result.attributes["clearance"] == "S"


def test_upsert_forwards_password_in_body(mock_client) -> None:
    """SDK passes password through as the body's `password` field."""
    from kamiwaza_sdk.services.subjects import SubjectsAPI

    mock_client.expect(
        "PUT",
        "/authz/subjects/bob",
        {
            "id": "kc-bob-uuid",
            "username": "bob",
            "attributes": {"clearance": "U"},
            "grants": [],
        },
    )

    SubjectsAPI(client=mock_client).upsert(
        "bob", attributes={"clearance": "U"}, password="initial-pw"
    )

    body = mock_client.calls[0][2].get("json", {})
    assert body == {"attributes": {"clearance": "U"}, "password": "initial-pw"}


def test_upsert_omits_password_field_when_none(mock_client) -> None:
    """When password=None, the field is omitted (T3.2 'don't touch creds')."""
    from kamiwaza_sdk.services.subjects import SubjectsAPI

    mock_client.expect(
        "PUT",
        "/authz/subjects/carol",
        {
            "id": "kc-carol-uuid",
            "username": "carol",
            "attributes": {"clearance": "C"},
            "grants": [],
        },
    )

    SubjectsAPI(client=mock_client).upsert("carol", attributes={"clearance": "C"})

    body = mock_client.calls[0][2].get("json", {})
    assert body == {"attributes": {"clearance": "C"}}


def test_upsert_url_encodes_special_char_username(mock_client) -> None:
    """PR-feedback M3: usernames with ``/``, ``@``, ``+``, space — all
    Keycloak-allowed — must be URL-encoded so the path doesn't split.

    Without ``quote(safe="")``, a username like ``svc/job-runner`` would
    resolve to ``/authz/subjects/svc/job-runner`` (two path segments)
    instead of ``/authz/subjects/svc%2Fjob-runner`` (one segment), and
    the request would miss the route.
    """
    from kamiwaza_sdk.services.subjects import SubjectsAPI

    username = "svc/job-runner+ops@kamiwaza"
    encoded = quote(username, safe="")
    # Sanity: encoding is NOT a no-op for this name.
    assert encoded != username

    mock_client.expect(
        "PUT",
        f"/authz/subjects/{encoded}",
        {"id": "kc-x", "username": username, "attributes": {}, "grants": []},
    )

    SubjectsAPI(client=mock_client).upsert(username, attributes={"role": "svc"})

    # Belt-and-suspenders: verify the recorded request path is the
    # encoded form, not the raw one.
    method, path, _ = mock_client.calls[0]
    assert method == "PUT"
    assert path == f"/authz/subjects/{encoded}"
    assert path != f"/authz/subjects/{username}"


def test_get_returns_subject(mock_client) -> None:
    """kz.subjects.get → GET /authz/subjects/{username} → Subject."""
    from kamiwaza_sdk.schemas.federation import Subject
    from kamiwaza_sdk.services.subjects import SubjectsAPI

    mock_client.expect(
        "GET",
        "/authz/subjects/dan",
        {
            "id": "kc-dan-uuid",
            "username": "dan",
            "attributes": {"clearance": "TS"},
            "grants": [],
        },
    )

    subject = SubjectsAPI(client=mock_client).get("dan")

    assert isinstance(subject, Subject)
    assert subject.id == "kc-dan-uuid"


def test_get_raises_on_404(mock_client) -> None:
    """404 subject_not_found surfaces as KamiwazaError per T5.10 mapping."""
    from kamiwaza_sdk.exceptions import KamiwazaError
    from kamiwaza_sdk.services.subjects import SubjectsAPI

    mock_client.raise_on(
        "GET",
        "/authz/subjects/ghost",
        KamiwazaError("subject_not_found", status_code=404),
    )

    with pytest.raises(KamiwazaError):
        SubjectsAPI(client=mock_client).get("ghost")


def test_delete_sends_delete_without_cascade_by_default(mock_client) -> None:
    """kz.subjects.delete defaults to no cascade — grants stay put."""
    from kamiwaza_sdk.services.subjects import SubjectsAPI

    mock_client.expect(
        "DELETE", "/authz/subjects/eve", {"deleted": True, "username": "eve"}
    )

    SubjectsAPI(client=mock_client).delete("eve")

    method, path, _ = mock_client.calls[0]
    assert method == "DELETE"
    assert path == "/authz/subjects/eve"


def test_delete_with_cascade_grants_passes_query_param(mock_client) -> None:
    """cascade_grants=True adds ?cascade=grants per T3.5 server contract."""
    from kamiwaza_sdk.services.subjects import SubjectsAPI

    mock_client.expect(
        "DELETE",
        "/authz/subjects/frank?cascade=grants",
        {"deleted": True, "username": "frank"},
    )

    SubjectsAPI(client=mock_client).delete("frank", cascade_grants=True)


# ─── Grants sub-resource ──────────────────────────────────────────────────


def test_grants_create_posts_to_subject_scoped_endpoint(mock_client) -> None:
    """kz.subjects.grants('alice').create(...) → POST /authz/subjects/alice/grants."""
    from kamiwaza_sdk.schemas.federation import Grant
    from kamiwaza_sdk.services.subjects import SubjectsAPI

    mock_client.expect(
        "POST",
        "/authz/subjects/alice/grants",
        {
            "object_namespace": "cluster",
            "object_id": "cluster-uuid-1",
            "relation": "viewer",
        },
    )

    grant = (
        SubjectsAPI(client=mock_client)
        .grants("alice")
        .create(
            object_namespace="cluster",
            object_id="cluster-uuid-1",
            relation="viewer",
        )
    )

    assert isinstance(grant, Grant)
    assert grant.relation == "viewer"

    body = mock_client.calls[0][2].get("json", {})
    assert body == {
        "object_namespace": "cluster",
        "object_id": "cluster-uuid-1",
        "relation": "viewer",
    }


def test_grants_list_returns_typed_grants(mock_client) -> None:
    """kz.subjects.grants('alice').list() returns list[Grant]."""
    from kamiwaza_sdk.schemas.federation import Grant
    from kamiwaza_sdk.services.subjects import SubjectsAPI

    mock_client.expect(
        "GET",
        "/authz/subjects/alice/grants",
        [
            {"object_namespace": "cluster", "object_id": "c1", "relation": "viewer"},
            {"object_namespace": "dataset", "object_id": "d1", "relation": "owner"},
        ],
    )

    grants = SubjectsAPI(client=mock_client).grants("alice").list()

    assert len(grants) == 2
    assert all(isinstance(g, Grant) for g in grants)
    assert {g.relation for g in grants} == {"viewer", "owner"}


def test_grants_delete_sends_tuple_key_in_body(mock_client) -> None:
    """kz.subjects.grants('alice').delete(...) → DELETE with tuple-key body."""
    from kamiwaza_sdk.services.subjects import SubjectsAPI

    mock_client.expect("DELETE", "/authz/subjects/alice/grants", {"deleted": True})

    SubjectsAPI(client=mock_client).grants("alice").delete(
        object_namespace="cluster",
        object_id="c1",
        relation="viewer",
    )

    body = mock_client.calls[0][2].get("json", {})
    assert body == {
        "object_namespace": "cluster",
        "object_id": "c1",
        "relation": "viewer",
    }
