"""Catalog container lifecycle and dataset membership through the SDK (ENG-12327, T06).

A member dataset and a container are created; the container is listed, read,
updated, has the dataset added and removed, and is read through its URN path
helper on the live ``/v2`` route along with the member (via
``catalog.encode_urn``); then the container and the member are each deleted
with the deletion proven by NotFound. How writes, deletes and the URN helpers
are proven is described in ``catalog_sdk_lifecycle_support``.
"""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import quote

import pytest
from kamiwaza_sdk.schemas.catalog import (
    ContainerCreate,
    ContainerUpdate,
    DatasetCreate,
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


def test_container_lifecycle_and_membership_through_container_client(
    live_kamiwaza_client, created
) -> None:
    client = live_kamiwaza_client
    containers = client.catalog.containers

    member_name = unique("sdk-t06-member")
    member_urn = client.catalog.datasets.create(
        DatasetCreate(
            name=member_name,
            platform="file",
            properties={"path": f"/tmp/{member_name}"},
        )
    )
    created.datasets[member_urn] = member_name
    wait_until(
        lambda: client.catalog.list_datasets(query=member_name),
        lambda items: any(item.urn == member_urn for item in items),
        f"catalog.list_datasets(query={member_name!r})",
    )
    # Positive control for the member's later NotFound: this URN is readable.
    member = client.catalog.datasets.get(member_urn)
    assert (member.urn, member.name) == (member_urn, member_name)

    name = unique("sdk-t06-container")
    description = "ENG-12327 T06 container"
    urn = containers.create(
        ContainerCreate(name=name, platform="file", description=description)
    )
    created.containers[urn] = name

    wait_until(
        lambda: client.catalog.list_containers(query=name),
        lambda items: any(item.urn == urn for item in items),
        f"catalog.list_containers(query={name!r})",
    )

    fetched = containers.get(urn)
    assert (fetched.urn, fetched.name, fetched.platform, fetched.description) == (
        urn,
        name,
        "file",
        description,
    )
    assert member_urn not in fetched.datasets

    updated_description = f"{description} (updated)"
    tag = unique("t06-tag")
    updated = containers.update(
        urn, ContainerUpdate(description=updated_description, tags=[tag])
    )
    assert updated.urn == urn
    wait_until(
        lambda: containers.get(urn),
        lambda item: item.description == updated_description and tag in item.tags,
        "containers.get after containers.update",
    )

    containers.add_dataset(urn, member_urn)
    wait_until(
        lambda: containers.get(urn),
        lambda item: member_urn in item.datasets,
        "container membership after containers.add_dataset",
    )

    containers.remove_dataset(urn, member_urn)
    wait_until(
        lambda: containers.get(urn),
        lambda item: member_urn not in item.datasets,
        "container membership after containers.remove_dataset",
    )

    encoded_container = containers.encode_path_urn(urn)
    assert encoded_container == quote(urn, safe="")
    container_v2 = client.get(f"/catalog/containers/v2/{encoded_container}")
    assert (container_v2["urn"], container_v2["name"]) == (urn, name)
    encoded_member = client.catalog.encode_urn(member_urn)
    assert encoded_member == quote(member_urn, safe="")
    member_v2 = client.get(f"/catalog/datasets/v2/{encoded_member}")
    assert (member_v2["urn"], member_v2["name"]) == (member_urn, member_name)

    containers.delete(urn)
    wait_until_absent(
        lambda: containers.get(urn), "containers.get after containers.delete"
    )
    del created.containers[urn]

    client.catalog.datasets.delete(member_urn)
    wait_until_absent(
        lambda: client.catalog.datasets.get(member_urn),
        "datasets.get for the member dataset after datasets.delete",
    )
    del created.datasets[member_urn]
