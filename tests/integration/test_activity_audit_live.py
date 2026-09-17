"""Live producer for the recorded activity and authorization-decision surfaces."""

import os
from uuid import uuid4

import pytest

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.authentication import UserPasswordAuthenticator
from kamiwaza_sdk.exceptions import APIError

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def _member_client(base_url: str) -> KamiwazaClient:
    username = os.getenv("KAMIWAZA_AUDIT_MEMBER_USERNAME", "")
    password = os.getenv("KAMIWAZA_AUDIT_MEMBER_PASSWORD", "")
    if not username or not password:
        pytest.skip("Non-admin audit test account is not configured")
    client = KamiwazaClient(base_url)
    client.authenticator = UserPasswordAuthenticator(
        username, password, client._auth_service
    )
    return client


def _assert_recorded_activity(client, before: set) -> None:
    activity = client.activity.get_recent_activity()
    assert any(
        item.id not in before
        and item.module == "catalog"
        and item.apicall.startswith("datasets/")
        for item in activity
    ), "Denied dataset read was not recorded in recent activity"


def _assert_recorded_decision(client, dataset_urn: str) -> None:
    audit = client.get("/auth/audit/decisions/export", params={"limit": 1000})
    assert any(
        event.get("object_id") == dataset_urn and event.get("result") == "deny"
        for event in audit["events"]
    ), "Denied dataset read was not recorded in decision audit"


def test_denied_dataset_read_appears_in_activity_and_decision_audit(
    live_kamiwaza_client,
    live_server_available: str,
) -> None:
    """A member's denied read leaves both a queryable activity and audit record."""
    admin = live_kamiwaza_client
    member = _member_client(live_server_available)
    before = {item.id for item in admin.activity.get_recent_activity()}
    name = f"sdk-audit-{uuid4().hex[:8]}"
    dataset = admin.catalog.create_dataset(
        name,
        platform="s3",
        properties={"path": f"s3://integration-tests/{name}.json"},
    )
    try:
        with pytest.raises(APIError) as denied:
            member.catalog.get_dataset(dataset.urn)
        assert denied.value.status_code in {403, 404}
        _assert_recorded_activity(admin, before)
        _assert_recorded_decision(admin, dataset.urn)
    finally:
        admin.catalog.datasets.delete(dataset.urn)
