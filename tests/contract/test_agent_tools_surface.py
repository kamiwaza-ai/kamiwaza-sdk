"""Contract tests for the agent tool surface.

These assert the promises the MCP server makes to hosts it did not write, so a
change here is a change to somebody else's integration:

* the index reaches the whole callable surface, by count (SC-029);
* a method added to the client is reachable with no server release (SC-029);
* no published identifier is the interface document's generated operation id
  (SC-031);
* every withheld operation names its reason rather than reporting "unknown"
  (SC-032).
"""

from __future__ import annotations

import inspect
import json
import warnings
from pathlib import Path

import pytest

from kamiwaza_sdk.agent_tools.catalog import build_catalog
from kamiwaza_sdk.agent_tools.ids import (
    UNPUBLISHED,
    UnpublishedOperationError,
    published_id,
)
from kamiwaza_sdk.agent_tools.spec_index import build_index

pytestmark = pytest.mark.contract


@pytest.fixture(scope="module")
def client():
    from kamiwaza_sdk.client import KamiwazaClient

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return KamiwazaClient(base_url="http://localhost:7777/api")


@pytest.fixture(scope="module")
def index(client):
    return build_index(client)


@pytest.fixture(scope="module")
def interface_operation_ids() -> set[str]:
    # The vendored document, from the repository root. Read from there rather
    # than from package data because it is a development artifact: the wheel
    # ships only the generated description table (see
    # scripts/regenerate_interface_descriptions.py), and a contract test is
    # development.
    raw = (
        Path(__file__).resolve().parents[2] / "kamiwaza-openapi-spec.json"
    ).read_text(encoding="utf-8")
    document = json.loads(raw)
    return {
        operation["operationId"]
        for operations in document.get("paths", {}).values()
        if isinstance(operations, dict)
        for operation in operations.values()
        if isinstance(operation, dict) and "operationId" in operation
    }


def test_index_reaches_the_whole_callable_surface(client, index) -> None:
    """SC-029, as a count comparison rather than a sample.

    Counts nested platform sub-clients too, since those are as callable as any
    other operation. Before the index walked them, this test passed at 310
    while 38 operations — including every gate-package call — were unreachable.
    """

    def public_methods(obj) -> int:
        return sum(
            1
            for name, _ in inspect.getmembers(type(obj), predicate=inspect.isfunction)
            if not name.startswith("_")
        )

    reachable = 0
    for name, value in vars(type(client)).items():
        if not isinstance(value, property) or name.startswith("_"):
            continue
        service = getattr(client, name)
        reachable += public_methods(service)
        for attribute in dir(service):
            if attribute.startswith("_") or attribute == "client":
                continue
            nested = getattr(service, attribute, None)
            if nested is None or not type(nested).__module__.startswith("kamiwaza"):
                continue
            if hasattr(nested, "client") or hasattr(nested, "_client"):
                reachable += public_methods(nested)
    assert len(index) == reachable


def test_the_index_is_larger_than_the_interface_document(
    index, interface_operation_ids
) -> None:
    """FR-005's premise, asserted rather than assumed.

    If the document ever described more operations than the client exposes, the
    index source decision would need revisiting rather than silently standing.
    """
    assert len(index) > len(interface_operation_ids)


def test_no_published_identifier_is_a_generated_operation_id(
    index, interface_operation_ids
) -> None:
    """SC-031: those ids are unstable under route renames and cost 3x the tokens."""
    published = {entry.published_id for entry in index}
    assert published & interface_operation_ids == set()


def test_every_published_identifier_derives_from_its_selector(index) -> None:
    for entry in index:
        assert entry.published_id == published_id(entry.selector)


def test_a_method_added_to_the_client_needs_no_release(client) -> None:
    """SC-029: reachable the day it ships, with no curation and no release."""

    class LaterService:
        def do_something_new(self) -> None:
            """Do something the catalog table has never heard of."""

    class Extended(type(client)):
        @property
        def later(self):
            return LaterService()

    extended = object.__new__(Extended)
    extended.__dict__.update(client.__dict__)
    index = build_index(extended)
    entry = index.get("do_something_new_later")
    assert entry.summary == "Do something the catalog table has never heard of."
    catalog = build_catalog(index, extended)
    assert any(item.published_id == "do_something_new_later" for item in catalog)
    assert all(item.category != "" for item in catalog)


def test_every_withheld_operation_names_its_reason(index) -> None:
    """SC-032: "unknown" would send an agent looking for a workaround."""
    assert UNPUBLISHED, "the withheld set must not be empty"
    for selector in UNPUBLISHED:
        with pytest.raises(UnpublishedOperationError) as raised:
            index.get(selector)
        assert raised.value.reason.strip()
        assert "unknown" not in raised.value.reason.lower()


def test_no_withheld_operation_appears_in_the_catalog(client, index) -> None:
    catalog_selectors = {item.selector for item in build_catalog(index, client)}
    assert catalog_selectors & set(UNPUBLISHED) == set()


def test_the_catalog_carries_every_published_operation(client, index) -> None:
    catalog = build_catalog(index, client)
    assert len(catalog) == len(index.published)


def test_filtering_the_catalog_never_invents_an_entry(client, index) -> None:
    full = {item.published_id for item in build_catalog(index, client)}
    read_only = {
        item.published_id for item in build_catalog(index, client, read_only=True)
    }
    mutations = {
        item.published_id for item in build_catalog(index, client, read_only=False)
    }
    assert read_only | mutations == full
    assert read_only & mutations == set()


def test_the_shipped_descriptions_match_the_vendored_document() -> None:
    """The generated table is current, asserted without waiting for CI.

    The wheel ships a table generated from the vendored interface document
    rather than the document itself: the document is a development artifact
    the drift gate reads, and only these fields are needed at runtime — 41 KiB
    of 278 KiB when the split was made.

    Generated code needs a freshness check or it becomes a second, quietly
    wrong source. The drift workflow runs the generator and diffs the result;
    this asserts the same thing locally, so a spec update that skipped the
    generator fails before it reaches a reviewer.
    """
    import importlib.util

    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "regenerate_interface_descriptions.py"
    )
    spec = importlib.util.spec_from_file_location("regenerate_descriptions", script)
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)

    expected = json.dumps(generator.build_descriptions(), indent=2, sort_keys=True) + "\n"
    committed = generator.DESCRIPTIONS_PATH.read_text(encoding="utf-8")

    assert committed == expected, (
        "The shipped interface descriptions are stale. Run "
        "`python scripts/regenerate_interface_descriptions.py` and commit the "
        "result; the diff is a change to what every published tool says about "
        "itself."
    )
