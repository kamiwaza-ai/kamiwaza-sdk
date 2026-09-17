"""Catalog dataset and container lifecycle through the SDK clients (ENG-12327, T06).

Creates, reads, updates, membership changes and deletes go through
``client.catalog`` and its ``datasets`` / ``containers`` sub-clients, so the SDK
methods the 1.2.1 coverage plan names are the ones that run. Writes are proven
by reading back the fields each assertion names, and a delete is proven by
NotFound on a URN that was read successfully earlier in the same test.

The URN path helpers make no HTTP call of their own. Each helper's output must
equal the standard percent-encoding of the URN (``urllib.parse.quote`` with no
safe characters), and is then sent to the live ``/v2/{urn}`` route with the
generic ``client.get`` (no SDK method reads by v2 path), where the same entity
must come back. On 1.2.1 that route also accepts an unencoded URN, so the
encoding itself rests on the first check and the v2 read shows the platform
accepts the helper's output.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, TypeVar
from urllib.parse import quote
from uuid import uuid4

import pytest
from kamiwaza_sdk.exceptions import KamiwazaError, NotFoundError
from kamiwaza_sdk.schemas.catalog import (
    ContainerCreate,
    ContainerUpdate,
    DatasetCreate,
    DatasetUpdate,
    Schema,
    SchemaField,
)
from pydantic import ValidationError as SchemaValidationError

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
    """Poll a by-URN read until it raises NotFoundError. Any other error propagates.

    Callers read the same URN successfully before deleting it, so NotFound here
    cannot be a URN that was never readable.
    """
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
    """URNs a test created, each with the unique name it was created under.

    A URN leaves the registry only after its deletion is proven, so a delete
    that reports success but leaves the entity readable is still cleaned up.
    """

    datasets: dict[str, str] = field(default_factory=dict)
    containers: dict[str, str] = field(default_factory=dict)


def _delete_if_owned(
    read: Callable[[str], Any],
    delete: Callable[[str], None],
    urn: str,
    name: str,
    failures: list[str],
) -> None:
    """Delete ``urn`` only if it still carries the name this test created it with,
    then prove the deletion.

    An absent URN needs no cleanup. A URN that now names something else is left
    alone and reported, so cleanup never deletes a resource the test cannot
    prove it created. SDK errors and response-validation errors are collected so
    the remaining resources are still attempted.
    """
    try:
        current = read(urn)
    except NotFoundError:
        return
    except (KamiwazaError, SchemaValidationError) as exc:
        failures.append(f"could not read {urn} before cleanup: {exc!r}")
        return
    if current.name != name:
        failures.append(
            f"refused to delete {urn}: it is named {current.name!r}, not {name!r}"
        )
        return
    try:
        delete(urn)
    except NotFoundError:
        return
    except (KamiwazaError, SchemaValidationError) as exc:
        failures.append(f"could not delete {urn}: {exc!r}")
        return
    try:
        _wait_until_absent(lambda: read(urn), f"cleanup of {urn}")
    except (AssertionError, KamiwazaError, SchemaValidationError) as exc:
        failures.append(f"could not prove {urn} deleted: {exc!r}")


@pytest.fixture
def created(live_kamiwaza_client) -> Iterator[_Created]:
    """Delete what the test body did not prove deleted.

    Every registered resource is attempted even if an earlier cleanup fails; all
    failures are raised together, which pytest reports as a teardown error
    beside the test outcome rather than in place of it.
    """
    registry = _Created()
    yield registry
    catalog = live_kamiwaza_client.catalog
    failures: list[str] = []
    for urn, name in reversed(list(registry.containers.items())):
        _delete_if_owned(
            catalog.containers.get, catalog.containers.delete, urn, name, failures
        )
    for urn, name in reversed(list(registry.datasets.items())):
        _delete_if_owned(
            catalog.datasets.get, catalog.datasets.delete, urn, name, failures
        )
    if failures:
        raise AssertionError("catalog cleanup incomplete: " + "; ".join(failures))


def test_dataset_lifecycle_through_dataset_client(
    live_kamiwaza_client, created
) -> None:
    client = live_kamiwaza_client
    datasets = client.catalog.datasets
    name = _unique("sdk-t06-dataset")
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

    _wait_until(
        lambda: client.catalog.list_datasets(query=name),
        lambda items: any(item.urn == urn for item in items),
        f"catalog.list_datasets(query={name!r})",
    )

    fetched = datasets.get(urn)
    assert (fetched.urn, fetched.name, fetched.platform) == (urn, name, "file")
    assert fetched.description == description
    assert fetched.properties.get("path") == path

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
    assert encoded == quote(urn, safe="")
    via_v2 = client.get(f"/catalog/datasets/v2/{encoded}")
    assert (via_v2["urn"], via_v2["name"]) == (urn, name)

    datasets.delete(urn)
    _wait_until_absent(lambda: datasets.get(urn), "datasets.get after datasets.delete")
    del created.datasets[urn]


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
    created.datasets[member_urn] = member_name
    _wait_until(
        lambda: client.catalog.list_datasets(query=member_name),
        lambda items: any(item.urn == member_urn for item in items),
        f"catalog.list_datasets(query={member_name!r})",
    )
    # Positive control for the member's later NotFound: this URN is readable.
    member = client.catalog.datasets.get(member_urn)
    assert (member.urn, member.name) == (member_urn, member_name)

    name = _unique("sdk-t06-container")
    description = "ENG-12327 T06 container"
    urn = containers.create(
        ContainerCreate(name=name, platform="file", description=description)
    )
    created.containers[urn] = name

    _wait_until(
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

    encoded_container = containers.encode_path_urn(urn)
    assert encoded_container == quote(urn, safe="")
    container_v2 = client.get(f"/catalog/containers/v2/{encoded_container}")
    assert (container_v2["urn"], container_v2["name"]) == (urn, name)
    encoded_member = client.catalog.encode_urn(member_urn)
    assert encoded_member == quote(member_urn, safe="")
    member_v2 = client.get(f"/catalog/datasets/v2/{encoded_member}")
    assert (member_v2["urn"], member_v2["name"]) == (member_urn, member_name)

    containers.delete(urn)
    _wait_until_absent(
        lambda: containers.get(urn), "containers.get after containers.delete"
    )
    del created.containers[urn]

    client.catalog.datasets.delete(member_urn)
    _wait_until_absent(
        lambda: client.catalog.datasets.get(member_urn),
        "datasets.get for the member dataset after datasets.delete",
    )
    del created.datasets[member_urn]
