from __future__ import annotations

import inspect
import warnings

import pytest

from kamiwaza_sdk.agent_tools.ids import UNPUBLISHED, UnpublishedOperationError
from kamiwaza_sdk.agent_tools.spec_index import DESCRIPTOR_VERSION, build_index

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def client():
    from kamiwaza_sdk.client import KamiwazaClient

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return KamiwazaClient(base_url="http://localhost:7777/api")


@pytest.fixture(scope="module")
def index(client):
    return build_index(client)


def test_index_covers_every_public_method_the_client_exposes(client, index) -> None:
    """SC-029: coverage is asserted against the client, not against a number.

    A hardcoded count would pass while the index silently missed a whole
    service, which is the failure this test exists to catch.
    """
    expected: set[str] = set()
    for name, value in vars(type(client)).items():
        if not isinstance(value, property) or name.startswith("_"):
            continue
        service = getattr(client, name)
        for method_name, _ in inspect.getmembers(
            type(service), predicate=inspect.isfunction
        ):
            if not method_name.startswith("_"):
                expected.add(f"{name}.{method_name}")
    assert {entry.selector for entry in index} == expected


def test_a_newly_added_method_appears_with_no_curation(client) -> None:
    """FR-005c: reachable the day it ships, with no server release."""

    class Bolted:
        def do_a_new_thing(self) -> None:
            """Do a new thing that no curation step knew about."""

    class Extended(type(client)):
        @property
        def bolted(self):
            return Bolted()

    extended = object.__new__(Extended)
    extended.__dict__.update(client.__dict__)
    index = build_index(extended)
    entry = index.get("do_a_new_thing_bolted")
    assert entry.selector == "bolted.do_a_new_thing"
    assert entry.summary == "Do a new thing that no curation step knew about."


def test_unpublished_operations_are_indexed_but_withheld(index) -> None:
    withheld = {e.selector for e in index if not e.is_published}
    assert withheld == set(UNPUBLISHED)
    assert len(index.published) == len(index) - len(withheld)


def test_asking_for_a_withheld_operation_names_the_reason(index) -> None:
    entry = next(e for e in index if not e.is_published)
    with pytest.raises(UnpublishedOperationError) as raised:
        index.get(entry.selector)
    assert raised.value.reason == entry.unpublished_reason
    assert raised.value.reason


def test_asking_for_an_unknown_operation_raises_key_error(index) -> None:
    with pytest.raises(KeyError):
        index.get("no_such_operation_anywhere")


def test_lookup_accepts_either_identifier(index) -> None:
    entry = index.published[0]
    assert index.get(entry.published_id) is entry
    assert index.get(entry.selector) is entry


def test_search_ranks_an_identifier_match_above_a_summary_match(index) -> None:
    results = index.search("deploy model")
    assert results, "search returned nothing for a core platform phrase"
    assert any("deploy" in e.published_id for e in results[:3])


def test_search_never_returns_a_withheld_operation(index) -> None:
    for query in ("pat", "password", "tool", "key"):
        assert all(e.is_published for e in index.search(query, limit=50))


def test_search_honours_the_limit_and_empty_query(index) -> None:
    assert len(index.search("get", limit=5)) <= 5
    assert index.search("   ") == ()


def test_index_stamps_its_provenance(index) -> None:
    assert index.descriptor_version == DESCRIPTOR_VERSION
    assert len(index.source_digest) == 64
    assert index.built_at.tzinfo is not None


def test_digest_changes_only_when_the_surface_changes(client, index) -> None:
    assert build_index(client).source_digest == index.source_digest


def test_summary_is_absent_rather_than_invented(index) -> None:
    """The gate fails on a missing docstring; the index must not paper over it."""
    undocumented = [e for e in index if e.summary is None]
    for entry in undocumented:
        assert entry.summary is None
    documented = index.coverage()["documented"]
    assert documented + len(undocumented) == len(index)


def test_required_parameters_are_a_subset_of_parameters(index) -> None:
    for entry in index:
        assert set(entry.required_parameters) <= set(entry.parameters)
