from __future__ import annotations

from uuid import uuid4

import pytest
import time
from pydantic import SecretStr

from kamiwaza_sdk.exceptions import APIError, NotFoundError
from kamiwaza_sdk.schemas.catalog import SecretCreate

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def _resolve_owner(client) -> str:
    try:
        profile = client.get("/auth/users/me")
    except Exception:  # pragma: no cover - live guard
        profile = {}
    username = (profile.get("username") or "sdk-integration").replace("@", "-")
    return profile.get("urn") or f"urn:li:corpuser:{username}"


def test_catalog_dataset_lifecycle(live_kamiwaza_client):
    client = live_kamiwaza_client
    dataset_name = _unique("sdk-dataset")
    dataset_urn: str | None = None

    try:
        dataset = client.catalog.create_dataset(
            dataset_name,
            platform="s3",
            description="Integration smoke dataset",
            properties={"path": f"s3://integration-tests/{dataset_name}.json"},
        )
        dataset_urn = dataset.urn

        fetched = client.catalog.get_dataset(dataset_urn)
        assert fetched.properties.get("path"), "Dataset should expose path"
        assert fetched.properties.get("location"), "Catalog helper should backfill location"
    finally:
        if dataset_urn:
            try:
                client.catalog.datasets.delete(dataset_urn)
            except APIError:
                pass


def test_catalog_secret_lifecycle(live_kamiwaza_client):
    """Create, observe, delete, and prove absence of a governed secret."""
    client = live_kamiwaza_client
    secret_payload = SecretCreate(
        name=_unique("sdk-secret"),
        value=SecretStr("integration-secret"),
        owner=_resolve_owner(client),
    )
    secret_urn = client.catalog.secrets.create(secret_payload, clobber=True)

    try:
        secret = client.catalog.secrets.get(secret_urn)
        assert secret.name == secret_payload.name

        # Listing is eventually consistent. A timeout is a failed lifecycle,
        # not a skip: the capability promises the created secret is governed
        # and discoverable through the catalog.
        for attempt in range(15):
            secrets = client.catalog.list_secrets(query=secret_payload.name)
            if any(item.urn == secret_urn for item in secrets):
                break
            if attempt < 14:
                time.sleep(2)
        else:
            pytest.fail("newly created secret never appeared in catalog listing")
    finally:
        # Deletion is part of the evidenced lifecycle. Let API failures
        # propagate instead of turning a failed delete into a passing record.
        client.catalog.secrets.delete(secret_urn)

    # Prove deletion, allowing only the expected NotFound result to terminate
    # the eventual-consistency poll. Any other API error remains a test error.
    for attempt in range(15):
        try:
            client.catalog.secrets.get(secret_urn)
        except NotFoundError:
            break
        if attempt < 14:
            time.sleep(2)
    else:
        pytest.fail("deleted secret remained readable from the catalog")
