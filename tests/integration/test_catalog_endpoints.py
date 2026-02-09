from __future__ import annotations

import time
from urllib.parse import quote
from uuid import uuid4

import pytest
from pydantic import SecretStr

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.catalog import (
    ContainerCreate,
    ContainerUpdate,
    DatasetCreate,
    DatasetUpdate,
    Schema,
    SchemaField,
    SecretCreate,
)

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def _encode_urn(value: str) -> str:
    return quote(value, safe="")


def _resolve_owner(client) -> str:
    try:
        profile = client.get("/auth/users/me")
    except Exception:  # pragma: no cover - live guard
        profile = {}
    username = (profile.get("username") or "sdk-integration").replace("@", "-")
    return profile.get("urn") or f"urn:li:corpuser:{username}"


def _require_catalog_admin(client) -> None:
    try:
        client.get("/catalog/containers/")
    except APIError as exc:
        if exc.status_code in {401, 403}:
            pytest.skip("Catalog container endpoints require admin credentials")
        raise


def _create_dataset(client, name: str) -> str:
    payload = DatasetCreate(
        name=name,
        platform="file",
        description="SDK integration dataset",
        properties={"path": f"/tmp/{name}"},
    )
    return client.catalog.datasets.create(payload)


def _delete_dataset(client, urn: str) -> None:
    try:
        client.delete("/catalog/datasets/by-urn", params={"urn": urn})
    except APIError:
        pass


def _create_container(client, name: str) -> str:
    payload = ContainerCreate(
        name=name,
        platform="file",
        description="SDK integration container",
    )
    return client.catalog.containers.create(payload)


def _delete_container(client, urn: str) -> None:
    try:
        client.delete("/catalog/containers/by-urn", params={"urn": urn})
    except APIError:
        pass


def _create_secret(client, name: str) -> str:
    secret = SecretCreate(
        name=name,
        value=SecretStr("sdk-secret"),
        owner=_resolve_owner(client),
        description="SDK integration secret",
    )
    return client.catalog.secrets.create(secret, clobber=True)


def _delete_secret_by_urn(client, urn: str) -> None:
    try:
        client.delete("/catalog/secrets/by-urn", params={"urn": urn})
    except APIError:
        pass


def _wait_for_urn_in_list(fetch, expected_urn: str, label: str, *, required: bool = True) -> bool:
    attempts = 5
    delay = 2
    last_urns: list[str | None] = []
    for attempt in range(attempts):
        items = fetch()
        last_urns = [getattr(item, "urn", None) for item in items]
        if expected_urn in last_urns:
            return True
        if attempt < attempts - 1:
            time.sleep(delay)
    if required:
        pytest.fail(
            f"{label} not found after {attempts} attempts: expected={expected_urn}, observed={last_urns}"
        )
    return False


def test_catalog_metadata_and_health(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client

    metadata = client.get("/catalog/")
    assert metadata.get("service") == "Kamiwaza Catalog Service"

    health = client.get("/catalog/health")
    assert health.get("status") == "ok"


def test_catalog_dataset_schema_endpoints(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client
    dataset_urn: str | None = None

    try:
        dataset_name = _unique("sdk-dataset")
        dataset_urn = _create_dataset(client, dataset_name)

        _wait_for_urn_in_list(
            lambda: client.catalog.datasets.list(query=dataset_name),
            dataset_urn,
            f"Dataset list query '{dataset_name}'",
        )

        schema = Schema(
            name="sdk-schema",
            platform="file",
            fields=[SchemaField(name="col", type="string")],
        )
        try:
            client.put(
                "/catalog/datasets/by-urn/schema",
                params={"urn": dataset_urn},
                json=schema.model_dump(),
            )
        except APIError as exc:
            if exc.status_code in (404, 501):
                pytest.skip(
                    "Server defect: dataset schema update not supported "
                    "(see docs-local/0.10.0/00-server-defects.md)"
                )
            raise

        try:
            schema_response = client.get(
                "/catalog/datasets/by-urn/schema",
                params={"urn": dataset_urn},
            )
        except APIError as exc:
            if exc.status_code in (404, 501):
                pytest.skip(
                    "Server defect: dataset schema retrieval not supported "
                    "(see docs-local/0.10.0/00-server-defects.md)"
                )
            raise
        assert schema_response.get("name") == "sdk-schema"

        encoded = _encode_urn(dataset_urn)
        client.put(
            f"/catalog/datasets/v2/{encoded}/schema",
            json=schema.model_dump(),
        )
        v2_schema = client.get(f"/catalog/datasets/v2/{encoded}/schema")
        assert v2_schema.get("name") == "sdk-schema"
    finally:
        if dataset_urn:
            _delete_dataset(client, dataset_urn)


def test_catalog_dataset_variant_endpoints(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client
    dataset_urn: str | None = None

    try:
        dataset_name = _unique("sdk-dataset-variant")
        dataset_urn = _create_dataset(client, dataset_name)

        _wait_for_urn_in_list(
            lambda: client.catalog.datasets.list(query=dataset_name),
            dataset_urn,
            f"Dataset list query '{dataset_name}'",
        )

        encoded = _encode_urn(dataset_urn)
        v2_dataset = client.get(f"/catalog/datasets/v2/{encoded}")
        assert v2_dataset.get("urn") == dataset_urn

        update_payload = DatasetUpdate(description="SDK updated").model_dump(exclude_none=True)
        patched = client.patch(
            f"/catalog/datasets/v2/{encoded}",
            json=update_payload,
        )
        assert patched.get("description") == "SDK updated"

        path_dataset = client.get(f"/catalog/datasets/{encoded}")
        assert path_dataset.get("urn") == dataset_urn

        patched_path = client.patch(
            f"/catalog/datasets/{encoded}",
            json=DatasetUpdate(tags=["sdk-test"]).model_dump(exclude_none=True),
        )
        assert "sdk-test" in (patched_path.get("tags") or [])
    finally:
        if dataset_urn:
            _delete_dataset(client, dataset_urn)

def test_catalog_dataset_delete_variants(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client

    dataset_v2 = _create_dataset(client, _unique("sdk-dataset-v2"))
    dataset_path = _create_dataset(client, _unique("sdk-dataset-path"))

    try:
        encoded_v2 = _encode_urn(dataset_v2)
        client.delete(f"/catalog/datasets/v2/{encoded_v2}")

        encoded_path = _encode_urn(dataset_path)
        client.delete(f"/catalog/datasets/{encoded_path}")
    finally:
        _delete_dataset(client, dataset_v2)
        _delete_dataset(client, dataset_path)

def test_catalog_container_list_query(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client
    _require_catalog_admin(client)

    container_name = _unique("sdk-container-query")
    container_urn = _create_container(client, container_name)

    try:
        found = _wait_for_urn_in_list(
            lambda: client.catalog.containers.list(query=container_name),
            container_urn,
            f"Container list query '{container_name}'",
            required=False,
        )
        if not found:
            pytest.skip(
                "Server defect: container list query does not return newly created container "
                "(see docs-local/0.10.0/00-server-defects.md)"
            )
    finally:
        _delete_container(client, container_urn)


def test_catalog_container_by_urn_and_path_endpoints(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client
    _require_catalog_admin(client)

    dataset_urn = _create_dataset(client, _unique("sdk-container-dataset"))
    container_urn = _create_container(client, _unique("sdk-container"))
    container_path = _create_container(client, _unique("sdk-container-path"))

    try:
        by_urn = client.get("/catalog/containers/by-urn", params={"urn": container_urn})
        assert by_urn.get("urn") == container_urn

        updated = client.patch(
            "/catalog/containers/by-urn",
            params={"urn": container_urn},
            json=ContainerUpdate(description="SDK updated").model_dump(exclude_none=True),
        )
        assert updated.get("description") == "SDK updated"

        encoded_container = _encode_urn(container_urn)
        path_container = client.get(f"/catalog/containers/{encoded_container}")
        assert path_container.get("urn") == container_urn

        path_updated = client.patch(
            f"/catalog/containers/{encoded_container}",
            json=ContainerUpdate(description="SDK path update").model_dump(exclude_none=True),
        )
        assert path_updated.get("description") == "SDK path update"

        add_payload = {"dataset_urn": dataset_urn}
        client.post(
            "/catalog/containers/by-urn/datasets",
            params={"container_urn": container_urn},
            json=add_payload,
        )
        client.delete(
            "/catalog/containers/by-urn/datasets",
            params={"container_urn": container_urn, "dataset_urn": dataset_urn},
        )

        encoded_dataset = _encode_urn(dataset_urn)
        client.post(
            f"/catalog/containers/{encoded_container}/datasets",
            json=add_payload,
        )
        client.delete(
            f"/catalog/containers/{encoded_container}/datasets/{encoded_dataset}",
        )

        encoded_path = _encode_urn(container_path)
        client.delete(f"/catalog/containers/{encoded_path}")
    finally:
        _delete_container(client, container_urn)
        _delete_container(client, container_path)
        _delete_dataset(client, dataset_urn)

def test_catalog_container_v2_endpoints(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client
    _require_catalog_admin(client)

    dataset_urn = _create_dataset(client, _unique("sdk-container-v2-dataset"))
    container_urn = _create_container(client, _unique("sdk-container-v2"))

    try:
        encoded_container = _encode_urn(container_urn)
        try:
            v2_container = client.get(f"/catalog/containers/v2/{encoded_container}")
        except APIError as exc:
            detail = None
            if isinstance(exc.response_data, dict):
                detail = exc.response_data.get("detail")
            if exc.status_code == 400 and detail == "container_urn must start with 'urn:li:container:'":
                pytest.skip(
                    "Server defect: /catalog/containers/v2 endpoints reject simple container URNs "
                    "(see docs-local/0.10.0/00-server-defects.md)"
                )
            raise
        assert v2_container.get("urn") == container_urn

        v2_updated = client.patch(
            f"/catalog/containers/v2/{encoded_container}",
            json=ContainerUpdate(tags=["sdk-tag"]).model_dump(exclude_none=True),
        )
        assert "sdk-tag" in (v2_updated.get("tags") or [])

        add_payload = {"dataset_urn": dataset_urn}
        client.post(
            f"/catalog/containers/v2/{encoded_container}/datasets",
            json=add_payload,
        )
        encoded_dataset = _encode_urn(dataset_urn)
        client.delete(
            f"/catalog/containers/v2/{encoded_container}/datasets/{encoded_dataset}",
        )

        client.delete(f"/catalog/containers/v2/{encoded_container}")
    finally:
        _delete_container(client, container_urn)
        _delete_dataset(client, dataset_urn)


def test_catalog_secret_list(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client

    secret_main_name = _unique("sdk-secret-main")
    secret_main = _create_secret(client, secret_main_name)
    try:
        found = _wait_for_urn_in_list(
            lambda: client.catalog.secrets.list(query=secret_main_name),
            secret_main,
            f"Secret list query '{secret_main_name}'",
            required=False,
        )
        if not found:
            pytest.skip(
                "Server defect: secret list query does not return newly created secret "
                "(see docs-local/0.10.0/00-server-defects.md)"
            )
    finally:
        _delete_secret_by_urn(client, secret_main)


def test_catalog_secret_endpoints(live_kamiwaza_client) -> None:
    client = live_kamiwaza_client

    secret_main = _create_secret(client, _unique("sdk-secret-main"))
    secret_by_urn = _create_secret(client, _unique("sdk-secret-by-urn"))
    secret_v2 = _create_secret(client, _unique("sdk-secret-v2"))
    secret_path = _create_secret(client, _unique("sdk-secret-path"))

    try:
        by_urn = client.get("/catalog/secrets/by-urn", params={"urn": secret_main})
        assert by_urn.get("urn") == secret_main

        encoded_main = _encode_urn(secret_main)
        v2 = client.get(f"/catalog/secrets/v2/{encoded_main}")
        assert v2.get("urn") == secret_main

        path = client.get(f"/catalog/secrets/{encoded_main}")
        assert path.get("urn") == secret_main

        client.delete("/catalog/secrets/by-urn", params={"urn": secret_by_urn})

        encoded_v2 = _encode_urn(secret_v2)
        client.delete(f"/catalog/secrets/v2/{encoded_v2}")

        encoded_path = _encode_urn(secret_path)
        client.delete(f"/catalog/secrets/{encoded_path}")
    finally:
        _delete_secret_by_urn(client, secret_main)
        _delete_secret_by_urn(client, secret_by_urn)
        _delete_secret_by_urn(client, secret_v2)
        _delete_secret_by_urn(client, secret_path)
