"""Shared helpers for the ENG-12327 (T06) catalog lifecycle live tests.

``test_catalog_dataset_sdk_lifecycle_live.py`` and
``test_catalog_container_sdk_lifecycle_live.py`` hold one test each, so
evidence credited per test file covers only what that file's test ran.

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
from uuid import uuid4

from kamiwaza_sdk.exceptions import KamiwazaError, NotFoundError
from pydantic import ValidationError as SchemaValidationError

POLL_ATTEMPTS = 15
POLL_DELAY_SECONDS = 2.0

_T = TypeVar("_T")


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def wait_until(read: Callable[[], _T], done: Callable[[_T], bool], label: str) -> _T:
    """Poll ``read`` until ``done`` holds. The catalog is eventually consistent;
    running out of attempts fails the test rather than skipping it."""
    last: _T | None = None
    for attempt in range(POLL_ATTEMPTS):
        last = read()
        if done(last):
            return last
        if attempt < POLL_ATTEMPTS - 1:
            time.sleep(POLL_DELAY_SECONDS)
    raise AssertionError(
        f"{label}: not satisfied after {POLL_ATTEMPTS} attempts; last={last!r}"
    )


def wait_until_absent(read: Callable[[], object], label: str) -> None:
    """Poll a by-URN read until it raises NotFoundError. Any other error propagates.

    Callers read the same URN successfully before deleting it, so NotFound here
    cannot be a URN that was never readable.
    """
    for attempt in range(POLL_ATTEMPTS):
        try:
            read()
        except NotFoundError:
            return
        if attempt < POLL_ATTEMPTS - 1:
            time.sleep(POLL_DELAY_SECONDS)
    raise AssertionError(f"{label}: still readable after {POLL_ATTEMPTS} attempts")


@dataclass
class CatalogRegistry:
    """URNs a test created, each with the unique name it was created under.

    A URN leaves the registry only after its deletion is proven, so a delete
    that reports success but leaves the entity readable is still cleaned up.
    """

    datasets: dict[str, str] = field(default_factory=dict)
    containers: dict[str, str] = field(default_factory=dict)


def delete_if_owned(
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
    the remaining resources are still attempted; a NotFound from the delete call
    itself is not collected, because the read-back decides whether it worked.
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
    delete_answer = ""
    try:
        delete(urn)
    except NotFoundError as exc:
        # On 1.2.1 a refused catalog delete also answers 404 ("not found or could
        # not be deleted"), so NotFound here is not proof of absence: the
        # read-back below decides, and the 404 is kept for the failure message.
        delete_answer = f" (the delete answered {exc!r})"
    except (KamiwazaError, SchemaValidationError) as exc:
        failures.append(f"could not delete {urn}: {exc!r}")
        return
    try:
        wait_until_absent(lambda: read(urn), f"cleanup of {urn}")
    except (AssertionError, KamiwazaError, SchemaValidationError) as exc:
        failures.append(f"could not prove {urn} deleted: {exc!r}{delete_answer}")


def registry_with_cleanup(catalog) -> Iterator[CatalogRegistry]:
    """Body of each test file's ``created`` fixture: delete what the test body
    did not prove deleted.

    Every registered resource is attempted even if an earlier cleanup fails; all
    failures are raised together, which pytest reports as a teardown error
    beside the test outcome rather than in place of it.
    """
    registry = CatalogRegistry()
    yield registry
    failures: list[str] = []
    for urn, name in reversed(list(registry.containers.items())):
        delete_if_owned(
            catalog.containers.get, catalog.containers.delete, urn, name, failures
        )
    for urn, name in reversed(list(registry.datasets.items())):
        delete_if_owned(
            catalog.datasets.get, catalog.datasets.delete, urn, name, failures
        )
    if failures:
        raise AssertionError("catalog cleanup incomplete: " + "; ".join(failures))
