from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import SecretStr

from kamiwaza_sdk.exceptions import APIError
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


def test_catalog_dataset_and_secret_lifecycle(live_kamiwaza_client):
    client = live_kamiwaza_client
    dataset_name = _unique("sdk-dataset")
    dataset_urn: str | None = None
    secret_urn: str | None = None
    owner = _resolve_owner(client)

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

        secret_payload = SecretCreate(
            name=f"{dataset_name}-secret",
            value=SecretStr("integration-secret"),
            owner=owner,
        )
        secret_urn = client.catalog.secrets.create(secret_payload, clobber=True)
        secret = client.catalog.secrets.get(secret_urn)
        assert secret.name.endswith("-secret")

        secrets = client.catalog.list_secrets(query=dataset_name)
        assert any(item.urn == secret_urn for item in secrets)
    finally:
        if secret_urn:
            try:
                client.catalog.secrets.delete(secret_urn)
            except APIError:
                pass
        if dataset_urn:
            try:
                client.catalog.datasets.delete(dataset_urn)
            except APIError:
                pass
