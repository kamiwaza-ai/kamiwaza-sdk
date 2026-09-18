"""Explicit-fixture live checks for managed connectors and M365 access (ENG-12433).

Set KAMIWAZA_CONNECTOR_VERIFY_FIXTURE to a private JSON file. Neither test
uses the existing M365 registration for writes. Missing fixtures skip, so a
green default integration run is *not* capability evidence.

The optional ``managed`` object needs ``allow_deployment: true``, a dedicated
deployable ``manifest`` (including deployment image), and ``config``. The test
registers a unique type and instance, then deletes both in teardown. Its
connector image must be approved for this cluster before enabling it.

The optional ``m365`` object needs ``connector_id``, two distinct user PATs
(``user_a_api_key``, ``user_b_api_key``), ``workroom_a_id``, ``workroom_b_id``,
and ``a_item``, ``b_item_in_a``, ``b_item_in_b``, ``shared_item``. Each item must have
``node_id``, ``request`` (ConnectorContentRequest fields), and ``sha256`` of
approved synthetic content. User B must belong to both rooms, user A only to
A. A and B must have independent provider identities and fixture files with
distinct bytes; ``shared_item`` must be provider-readable by both users. The
tests never create or alter those tenant objects.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import pytest

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.connector_surfaces import (
    ConnectorContentRequest,
    ConnectorSurfaceRef,
)
from kamiwaza_sdk.schemas.connectors import (
    ConnectorCatalogRegister,
    ConnectorCreate,
    ConnectorUpdate,
)

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def _fixture(section: str) -> dict:
    path = os.environ.get("KAMIWAZA_CONNECTOR_VERIFY_FIXTURE")
    if not path:
        pytest.skip(
            "ENG-12433: private disposable connector/two-user M365 fixture not supplied"
        )
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get(section), dict):
        pytest.skip(f"ENG-12433: {section} fixture not supplied")
    return data[section]


def _require_fields(data: dict, *fields: str) -> None:
    missing = [field for field in fields if not data.get(field)]
    if missing:
        pytest.fail(f"ENG-12433 fixture missing required fields: {', '.join(missing)}")


def test_catalog_cleanup_ignores_only_missing_type() -> None:
    class FakeClient:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

        def delete(self, _path: str) -> None:
            raise APIError("catalog cleanup failed", status_code=self.status_code)

    _delete_catalog_if_present(FakeClient(404), "sdkverify-test")
    with pytest.raises(APIError) as failure:
        _delete_catalog_if_present(FakeClient(500), "sdkverify-test")
    assert failure.value.status_code == 500


def _delete_catalog_if_present(client, connector_type: str) -> None:
    try:
        # No typed unregister_type method exists yet in the SDK.
        client.delete(f"/connectors/catalog/{connector_type}")
    except APIError as exc:
        if exc.status_code != 404:
            raise


def test_disposable_managed_connector_lifecycle(request: pytest.FixtureRequest) -> None:
    fixture = _fixture("managed")
    if fixture.get("allow_deployment") is not True:
        pytest.skip(
            "ENG-12433: disposable connector deployment not explicitly approved"
        )
    _require_fields(fixture, "manifest")
    if "config" not in fixture or not isinstance(fixture["config"], dict):
        pytest.fail("ENG-12433 managed fixture needs an explicit config object")

    manifest = dict(fixture["manifest"])
    suffix = uuid4().hex[:12]
    connector_type = f"sdkverify-{suffix}"
    manifest["connector_type"] = connector_type
    deployment = manifest.get("deployment")
    if not isinstance(deployment, dict) or not deployment.get("image_repository"):
        pytest.fail("ENG-12433 managed fixture needs an approved image repository")

    client = request.getfixturevalue("live_kamiwaza_client")
    connector_id = None
    catalog_created = True
    try:
        entry = client.connectors.register_type(
            ConnectorCatalogRegister(manifest=manifest)
        )
        assert entry.connector_type == connector_type
        assert not entry.already_subscribed

        created = client.connectors.create(
            ConnectorCreate(
                name=f"SDK verification {suffix}",
                connector_type=connector_type,
                config=fixture.pop("config"),
                scopes=fixture.get("scopes", []),
            )
        )
        connector_id = created.id
        assert created.enabled
        assert created.connector_type == connector_type
        assert client.connectors.get(connector_id).id == connector_id

        renamed = client.connectors.update(
            connector_id, ConnectorUpdate(name=f"SDK verified {suffix}")
        )
        assert renamed.name == f"SDK verified {suffix}"

        deadline = monotonic() + 90
        while True:
            try:
                verification = client.connectors.verify_connection(connector_id)
                break
            except APIError as exc:
                if exc.status_code not in (502, 503, 504) or monotonic() >= deadline:
                    raise
                sleep(3)
        assert verification.available, "Disposable connector verification did not pass"

        assert any(
            str(item.id) == str(connector_id)
            for item in client.connectors.list_available()
        )
        disabled = client.connectors.update(
            connector_id, ConnectorUpdate(enabled=False)
        )
        assert not disabled.enabled
        assert all(
            str(item.id) != str(connector_id)
            for item in client.connectors.list_available()
        )
    finally:
        try:
            cleanup_ids = (
                [connector_id]
                if connector_id is not None
                else [
                    item.id
                    for item in client.connectors.list()
                    if item.connector_type == connector_type
                ]
            )
            for cleanup_id in cleanup_ids:
                client.connectors.delete(cleanup_id)
        finally:
            if catalog_created:
                _delete_catalog_if_present(client, connector_type)


def _fetch_digest(client, ref: ConnectorSurfaceRef, item: dict) -> str:
    _require_fields(item, "node_id", "request", "sha256")
    request = ConnectorContentRequest.model_validate(item["request"])
    assert request.surface == "files"
    content = client.connectors.fetch_surface_content(ref, item["node_id"], request)
    return hashlib.sha256(content.content).hexdigest()


def _expect_denied(client, ref: ConnectorSurfaceRef, item: dict) -> None:
    _require_fields(item, "node_id", "request")
    request = ConnectorContentRequest.model_validate(item["request"])
    assert request.surface == "files"
    with pytest.raises(APIError) as denied:
        client.connectors.fetch_surface_content(ref, item["node_id"], request)
    assert denied.value.status_code in (403, 404)


def test_m365_workroom_and_provider_access(request: pytest.FixtureRequest) -> None:
    fixture = _fixture("m365")
    _require_fields(
        fixture,
        "connector_id",
        "user_a_api_key",
        "user_b_api_key",
        "workroom_a_id",
        "workroom_b_id",
        "a_item",
        "b_item_in_a",
        "b_item_in_b",
        "shared_item",
    )
    assert fixture["workroom_a_id"] != fixture["workroom_b_id"]

    live_server_available = request.getfixturevalue("live_server_available")
    live_kamiwaza_client = request.getfixturevalue("live_kamiwaza_client")
    verify_tls = live_kamiwaza_client.session.verify
    a = KamiwazaClient(
        live_server_available, api_key=fixture.pop("user_a_api_key"), verify=verify_tls
    )
    b = KamiwazaClient(
        live_server_available, api_key=fixture.pop("user_b_api_key"), verify=verify_tls
    )
    identity_a = a.auth.get_current_user()
    identity_b = b.auth.get_current_user()
    assert identity_a.sub != identity_b.sub, (
        "Fixture PATs must belong to different users"
    )

    ref_a = ConnectorSurfaceRef(
        workroom_id=fixture["workroom_a_id"], connector_id=fixture["connector_id"]
    )
    ref_b = ConnectorSurfaceRef(
        workroom_id=fixture["workroom_b_id"], connector_id=fixture["connector_id"]
    )
    assert any(
        str(item.id) == str(ref_a.connector_id)
        for item in a.connectors.list_surface_catalog(
            ref_a.workroom_id, connected_only=True
        )
    )
    assert any(
        str(item.id) == str(ref_a.connector_id)
        for item in b.connectors.list_surface_catalog(
            ref_a.workroom_id, connected_only=True
        )
    )
    assert any(
        str(item.id) == str(ref_b.connector_id)
        for item in b.connectors.list_surface_catalog(
            ref_b.workroom_id, connected_only=True
        )
    )
    with pytest.raises(APIError) as denied_catalog:
        a.connectors.list_surface_catalog(ref_b.workroom_id, connected_only=True)
    assert denied_catalog.value.status_code in (403, 404)

    items = (
        fixture["a_item"],
        fixture["b_item_in_a"],
        fixture["b_item_in_b"],
        fixture["shared_item"],
    )
    for item in items:
        _require_fields(item, "node_id", "request", "sha256")
    expected = [item["sha256"].lower() for item in items]
    assert len(set(expected)) == len(items), (
        "Fixture files need distinct approved content"
    )
    for client, ref, item in (
        (a, ref_a, items[0]),
        (b, ref_a, items[1]),
        (b, ref_b, items[2]),
        (a, ref_a, items[3]),
        (b, ref_b, items[3]),
    ):
        assert _fetch_digest(client, ref, item) == item["sha256"].lower()

    # B is a member of A's room but provider ACL must reject A's item.
    _expect_denied(b, ref_a, items[0])
    # A can provider-read this shared item in A, but cannot use room B.
    _expect_denied(a, ref_b, items[3])
