from __future__ import annotations

import warnings

import pytest

from kamiwaza_sdk.agent_tools.descriptors import (
    DescriptionSource,
    description_coverage,
    interface_descriptions,
    resolve_description,
)
from kamiwaza_sdk.agent_tools.spec_index import OperationEntry, build_index

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


def _entry(**overrides) -> OperationEntry:
    defaults = {
        "selector": "models.list_models",
        "published_id": "list_models",
        "service": "models",
        "method": "list_models",
        "summary": None,
        "parameters": (),
        "required_parameters": (),
        "returns": None,
    }
    return OperationEntry(**{**defaults, **overrides})


def test_the_interface_document_ships_with_the_package() -> None:
    """FR-005a: the consumer installs the wheel, so the document must be in it.

    A vendored copy in the consuming server would be a second source of truth,
    and an absent document would silently degrade every description.
    """
    assert len(interface_descriptions()) == 233


def test_docstring_wins_over_the_interface_document(client) -> None:
    """FR-006h precedence, inverted from the reference implementation on purpose."""
    entry = _entry(summary="List all models, optionally including files.")
    description, source = resolve_description(entry, client.models)
    assert source is DescriptionSource.DOCSTRING
    assert description == "List all models, optionally including files."


def test_interface_description_is_used_when_no_docstring(client, index) -> None:
    resolved = [
        resolve_description(entry, getattr(client, entry.service, None))
        for entry in index.published
        if not entry.summary
    ]
    sources = {source for _, source in resolved}
    assert DescriptionSource.INTERFACE_DESCRIPTION in sources
    assert DescriptionSource.INTERFACE_SUMMARY in sources


def test_absent_is_reported_rather_than_invented(client) -> None:
    """The gate needs the gap; a plausible sentence would hide it."""
    entry = _entry(method="method_that_does_not_exist")
    description, source = resolve_description(entry, client.models)
    assert description is None
    assert source is DescriptionSource.ABSENT


def test_resolution_without_a_service_falls_back_to_the_docstring_only() -> None:
    assert resolve_description(_entry(summary="Do the thing properly.")) == (
        "Do the thing properly.",
        DescriptionSource.DOCSTRING,
    )
    assert resolve_description(_entry()) == (None, DescriptionSource.ABSENT)


def test_resolution_issues_no_request(client, monkeypatch) -> None:
    """Resolving a description must never touch the network."""

    def explode(*args, **kwargs):
        raise AssertionError("resolution performed a request")

    monkeypatch.setattr(type(client), "_request", explode, raising=False)
    entry = _entry(summary=None, method="change_my_password", service="auth")
    resolve_description(entry, client.auth)


def test_coverage_counts_every_published_operation(client, index) -> None:
    counts = description_coverage(index, client)
    accounted = sum(counts[source.value] for source in DescriptionSource)
    assert accounted == len(index.published)


def test_every_published_operation_resolves_a_description(client, index) -> None:
    """The state T116 was written to reach, now asserted rather than tracked.

    An earlier version of this test required ``absent > 0`` on the grounds that
    a zero meant the gate had nothing to enforce. That was true of the
    measurement and false of the gate: a ceiling of zero fails the next
    operation added without a description, which is exactly the enforcement
    wanted.
    """
    counts = description_coverage(index, client)
    assert counts["absent"] == 0
    assert counts["docstring"] > counts["interface_description"], (
        "docstrings must be the dominant source, or the precedence is wrong "
        "for this repository"
    )
