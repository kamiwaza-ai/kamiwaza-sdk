"""Catalog dataset and container lifecycle through the SDK clients (ENG-12327, T06).

Every read, update, membership change and delete below goes through
``client.catalog`` and its ``datasets`` / ``containers`` sub-clients rather than
raw HTTP, so the SDK methods the 1.2.1 coverage plan names are the ones that
run. Each write is proven by reading it back through a separate call, and each
delete is proven by absence.

The URN path helpers make no HTTP call of their own. They are exercised by
sending their output to the live ``/v2/{urn}`` route and asserting the same
entity comes back.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import TypeVar
from uuid import uuid4

import pytest
from kamiwaza_sdk.exceptions import NotFoundError
from kamiwaza_sdk.schemas.catalog import (
    ContainerCreate,
    ContainerUpdate,
    DatasetCreate,
    DatasetUpdate,
    Schema,
    SchemaField,
)

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

_POLL_ATTEMPTS = 15
_POLL_DELAY_SECONDS = 2.0

_T = TypeVar("_T")


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def _wait_until(read: Callable[[], _T], done: Callable[[_T], bool], label: str) -> _T:
    """Poll ``read`` until ``done`` holds. The catalog is eventually consistent;
    running out of attempts fails the test rather than skipping it."""
    last: _T | None = None
    for attempt in range(_POLL_ATTEMPTS):
        last = read()
        if done(last):
            return last
        if attempt < _POLL_ATTEMPTS - 1:
            time.sleep(_POLL_DELAY_SECONDS)
    raise AssertionError(
        f"{label}: not satisfied after {_POLL_ATTEMPTS} attempts; last={last!r}"
    )


def _wait_until_absent(read: Callable[[], object], label: str) -> None:
    """Poll a by-URN read until it raises NotFoundError. Any other error propagates."""
    for attempt in range(_POLL_ATTEMPTS):
        try:
            read()
        except NotFoundError:
            return
        if attempt < _POLL_ATTEMPTS - 1:
            time.sleep(_POLL_DELAY_SECONDS)
    raise AssertionError(f"{label}: still readable after {_POLL_ATTEMPTS} attempts")


@dataclass
class _Created:
    """Catalog resources a test created and has not deleted itself."""

    datasets: list[str] = field(default_factory=list)
    containers: list[str] = field(default_factory=list)


@pytest.fixture
def created(live_kamiwaza_client) -> Iterator[_Created]:
    """Delete whatever the test body left behind.

    On the passing path the body deletes and proves absence itself, so nothing
    is left. On a failing path this removes the rest; a delete error here is
    reported by pytest as a teardown error beside the test failure, never
    in place of it.
    """
    registry = _Created()
    yield registry
    catalog = live_kamiwaza_client.catalog
    for urn in reversed(registry.containers):
        catalog.containers.delete(urn)
    for urn in reversed(registry.datasets):
        catalog.datasets.delete(urn)


def test_dataset_lifecycle_through_dataset_client(
    live_kamiwaza_client, created
) -> None:
    client = live_kamiwaza_client
    datasets = client.catalog.datasets
    name = _unique("sdk-t06-dataset")
    description = "ENG-12327 T06 dataset"

    urn = datasets.create(
        DatasetCreate(
            name=name,
            platform="file",
            description=description,
            properties={"path": f"/tmp/{name}"},
        )
    )
    created.datasets.append(urn)

    _wait_until(
        lambda: client.catalog.list_datasets(query=name),
        lambda items: any(item.urn == urn for item in items),
        f"catalog.list_datasets(query={name!r})",
    )

    fetched = datasets.get(urn)
    assert (fetched.urn, fetched.name, fetched.platform) == (urn, name, "file")
    assert fetched.description == description

    updated_description = f"{description} (updated)"
    tag = _unique("t06-tag")
    updated = datasets.update(
        urn, DatasetUpdate(description=updated_description, tags=[tag])
    )
    assert updated.urn == urn
    _wait_until(
        lambda: datasets.get(urn),
        lambda item: item.description == updated_description and tag in item.tags,
        "datasets.get after datasets.update",
    )

    schema = Schema(
        name=_unique("sdk-t06-schema"),
        platform="file",
        fields=[
            SchemaField(name=f"col_{uuid4().hex[:8]}", type="string"),
            SchemaField(name="amount", type="number"),
        ],
    )
    datasets.update_schema(urn, schema)
    stored = _wait_until(
        lambda: datasets.get_schema(urn),
        lambda item: item.name == schema.name,
        "datasets.get_schema after datasets.update_schema",
    )
    assert sorted((f.name, f.type) for f in stored.fields) == sorted(
        (f.name, f.type) for f in schema.fields
    )

    encoded = datasets.encode_path_urn(urn)
    assert encoded == client.catalog.encode_urn(urn)
    assert encoded != urn, (
        "a dataset URN carries characters that must be percent-encoded"
    )
    via_v2 = client.get(f"/catalog/datasets/v2/{encoded}")
    assert (via_v2["urn"], via_v2["name"]) == (urn, name)

    datasets.delete(urn)
    created.datasets.remove(urn)
    _wait_until_absent(lambda: datasets.get(urn), "datasets.get after datasets.delete")
    _wait_until(
        lambda: client.catalog.list_datasets(query=name),
        lambda items: all(item.urn != urn for item in items),
        "catalog.list_datasets after datasets.delete",
    )


def test_container_lifecycle_and_membership_through_container_client(
    live_kamiwaza_client, created
) -> None:
    client = live_kamiwaza_client
    containers = client.catalog.containers

    member_name = _unique("sdk-t06-member")
    member_urn = client.catalog.datasets.create(
        DatasetCreate(
            name=member_name,
            platform="file",
            properties={"path": f"/tmp/{member_name}"},
        )
    )
    created.datasets.append(member_urn)

    name = _unique("sdk-t06-container")
    description = "ENG-12327 T06 container"
    urn = containers.create(
        ContainerCreate(name=name, platform="file", description=description)
    )
    created.containers.append(urn)

    _wait_until(
        lambda: client.catalog.list_containers(query=name),
        lambda items: any(item.urn == urn for item in items),
        f"catalog.list_containers(query={name!r})",
    )

    fetched = containers.get(urn)
    assert (fetched.urn, fetched.name, fetched.description) == (urn, name, description)
    assert member_urn not in fetched.datasets

    updated_description = f"{description} (updated)"
    tag = _unique("t06-tag")
    updated = containers.update(
        urn, ContainerUpdate(description=updated_description, tags=[tag])
    )
    assert updated.urn == urn
    _wait_until(
        lambda: containers.get(urn),
        lambda item: item.description == updated_description and tag in item.tags,
        "containers.get after containers.update",
    )

    containers.add_dataset(urn, member_urn)
    _wait_until(
        lambda: containers.get(urn),
        lambda item: member_urn in item.datasets,
        "container membership after containers.add_dataset",
    )

    containers.remove_dataset(urn, member_urn)
    _wait_until(
        lambda: containers.get(urn),
        lambda item: member_urn not in item.datasets,
        "container membership after containers.remove_dataset",
    )

    encoded = containers.encode_path_urn(urn)
    assert encoded == client.catalog.encode_urn(urn)
    assert encoded != urn, (
        "a container URN carries characters that must be percent-encoded"
    )
    via_v2 = client.get(f"/catalog/containers/v2/{encoded}")
    assert (via_v2["urn"], via_v2["name"]) == (urn, name)

    containers.delete(urn)
    created.containers.remove(urn)
    _wait_until_absent(
        lambda: containers.get(urn), "containers.get after containers.delete"
    )
    _wait_until(
        lambda: client.catalog.list_containers(query=name),
        lambda items: all(item.urn != urn for item in items),
        "catalog.list_containers after containers.delete",
    )
