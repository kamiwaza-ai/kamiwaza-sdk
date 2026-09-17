"""Catalog dataset lifecycle through the SDK dataset client (ENG-12327, T06).

A dataset is created, found by listing, read, updated, given a schema, read
through its URN path helper on the live ``/v2`` route, and deleted with the
deletion proven by NotFound. How writes, deletes and the URN helper are proven
is described in ``catalog_sdk_lifecycle_support``.
"""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import quote
from uuid import uuid4

import pytest
from kamiwaza_sdk.schemas.catalog import (
    DatasetCreate,
    DatasetUpdate,
    Schema,
    SchemaField,
)
from tests.integration.catalog_sdk_lifecycle_support import (
    CatalogRegistry,
    registry_with_cleanup,
    unique,
    wait_until,
    wait_until_absent,
)

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


@pytest.fixture
def created(live_kamiwaza_client) -> Iterator[CatalogRegistry]:
    """Delete what the test body did not prove deleted."""
    yield from registry_with_cleanup(live_kamiwaza_client.catalog)


def test_dataset_lifecycle_through_dataset_client(
    live_kamiwaza_client, created
) -> None:
    client = live_kamiwaza_client
    datasets = client.catalog.datasets
    name = unique("sdk-t06-dataset")
    description = "ENG-12327 T06 dataset"
    path = f"/tmp/{name}"

    urn = datasets.create(
        DatasetCreate(
            name=name,
            platform="file",
            description=description,
            properties={"path": path},
        )
    )
    created.datasets[urn] = name

    wait_until(
        lambda: client.catalog.list_datasets(query=name),
        lambda items: any(item.urn == urn for item in items),
        f"catalog.list_datasets(query={name!r})",
    )

    fetched = datasets.get(urn)
    assert (fetched.urn, fetched.name, fetched.platform) == (urn, name, "file")
    assert fetched.description == description
    assert fetched.properties.get("path") == path

    updated_description = f"{description} (updated)"
    tag = unique("t06-tag")
    updated = datasets.update(
        urn, DatasetUpdate(description=updated_description, tags=[tag])
    )
    assert updated.urn == urn
    wait_until(
        lambda: datasets.get(urn),
        lambda item: item.description == updated_description and tag in item.tags,
        "datasets.get after datasets.update",
    )

    schema = Schema(
        name=unique("sdk-t06-schema"),
        platform="file",
        fields=[
            SchemaField(name=f"col_{uuid4().hex[:8]}", type="string"),
            SchemaField(name="amount", type="number"),
        ],
    )
    datasets.update_schema(urn, schema)
    stored = wait_until(
        lambda: datasets.get_schema(urn),
        lambda item: item.name == schema.name,
        "datasets.get_schema after datasets.update_schema",
    )
    assert sorted((f.name, f.type) for f in stored.fields) == sorted(
        (f.name, f.type) for f in schema.fields
    )

    encoded = datasets.encode_path_urn(urn)
    assert encoded == quote(urn, safe="")
    via_v2 = client.get(f"/catalog/datasets/v2/{encoded}")
    assert (via_v2["urn"], via_v2["name"]) == (urn, name)

    datasets.delete(urn)
    wait_until_absent(lambda: datasets.get(urn), "datasets.get after datasets.delete")
    del created.datasets[urn]
