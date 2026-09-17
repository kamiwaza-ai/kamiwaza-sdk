from __future__ import annotations

import json
import warnings

import pytest

from kamiwaza_sdk.agent_tools.catalog import (
    DETAIL_LEVELS,
    build_catalog,
    categories,
    measure_cost,
)
from kamiwaza_sdk.agent_tools.spec_index import build_index

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


@pytest.fixture(scope="module")
def catalog(index, client):
    return build_catalog(index, client)


def test_every_operation_lands_in_a_category(catalog) -> None:
    """An uncategorised entry is unfilterable, which defeats the escape hatch."""
    assert all(entry.category for entry in catalog)
    assert "other" not in categories(catalog), (
        "a service is missing from the category table; add it rather than "
        "leaving its operations in 'other'"
    )


def test_filtering_by_category_returns_only_that_category(index, client) -> None:
    models = build_catalog(index, client, category="models")
    assert models
    assert {entry.category for entry in models} == {"models"}


def test_filtering_by_read_only_splits_the_catalog(index, client, catalog) -> None:
    reads = build_catalog(index, client, read_only=True)
    writes = build_catalog(index, client, read_only=False)
    assert len(reads) + len(writes) == len(catalog)
    assert all(entry.read_only for entry in reads)
    assert not any(entry.read_only for entry in writes)


def test_filters_compose(index, client) -> None:
    subset = build_catalog(index, client, category="models", read_only=True)
    assert subset
    assert all(entry.category == "models" and entry.read_only for entry in subset)


def test_definition_carries_neutral_field_names(catalog) -> None:
    """Protocol field names change with the revision; these must not."""
    definition = catalog[0].as_definition()
    assert set(definition) == {
        "id",
        "category",
        "description",
        "requires_approval",
        "hints",
        "parameters",
        "required",
    }
    assert set(definition["hints"]) == {
        "read_only",
        "destructive",
        "idempotent",
        "open_world",
    }
    assert "inputSchema" not in definition
    assert "annotations" not in definition


def test_definition_is_json_serialisable(catalog) -> None:
    json.dumps([entry.as_definition() for entry in catalog])


def test_a_level_carries_only_what_it_promises(catalog) -> None:
    """Each level is what its name says, and every level identifies the entry."""
    entry = catalog[0]
    assert entry.as_definition("names") == {"id": entry.published_id}
    assert set(entry.as_definition("brief")) == {"id", "category", "description"}
    assert entry.as_definition() == entry.as_definition("full")


def test_a_cheaper_level_is_a_prefix_of_the_fuller_one(catalog) -> None:
    """A host parses one element type at every level, so a field that appears
    at two levels is the same field with the same name and the same value."""
    for entry in catalog:
        full = entry.as_definition("full")
        for level in ("names", "brief"):
            projection = entry.as_definition(level)
            assert projection == {key: full[key] for key in projection}


def test_an_unknown_level_is_refused_by_name(catalog) -> None:
    """A typo has to fail where it was made. A silent fall back to ``full``
    would hand a host the most expensive answer to a request for the cheapest,
    which is the opposite of what asking for a level is for."""
    with pytest.raises(ValueError, match="names, brief, full"):
        catalog[0].as_definition("summary")  # type: ignore[arg-type]


def test_every_level_is_json_serialisable(catalog) -> None:
    for level in DETAIL_LEVELS:
        json.dumps([entry.as_definition(level) for entry in catalog])


def test_a_cheaper_level_costs_less(catalog) -> None:
    """The reason the levels exist, measured rather than asserted: each one
    strictly cheaper than the next, so a host choosing a level is choosing a
    cost."""
    costs = [measure_cost(catalog, len, level)["total"] for level in DETAIL_LEVELS]
    assert costs == sorted(costs)
    assert costs[0] * 4 < costs[-1], (
        f"the cheapest level costs {costs[0]} characters against the fullest's "
        f"{costs[-1]}, which is not the order of magnitude that makes asking "
        f"for a level worth a host's trouble"
    )


def test_cost_is_priced_at_the_level_it_is_asked_for(catalog) -> None:
    """A cost measured at one level must not be reported for another: the
    number is what a host budgets against."""
    priced = measure_cost(catalog, len, "names")
    counted = sum(len(json.dumps(e.as_definition("names"), separators=(",", ":"))) for e in catalog)
    assert priced["total"] == counted


def test_cost_is_measured_with_the_callers_tokeniser(catalog) -> None:
    """The tokeniser is an argument so this package needs no such dependency."""
    measured = measure_cost(catalog, lambda text: len(text.split()))
    assert measured["entries"] == len(catalog)
    assert measured["total"] > 0
    assert measured["per_entry"] == measured["total"] // measured["entries"]


def test_cost_of_an_empty_catalog_is_zero() -> None:
    assert measure_cost((), lambda text: len(text)) == {
        "entries": 0,
        "total": 0,
        "per_entry": 0,
    }


def test_measured_cost_justifies_not_defaulting_to_the_catalog(catalog) -> None:
    """FR-001's premise, measured: the catalog is an order of magnitude larger
    than the fixed surface's 2,000-token budget, which is why it is opt-in."""
    # The character count is exact and runs everywhere; the token count needs
    # tiktoken's table, which `get_encoding` downloads on a machine that has
    # not cached it — and CI blocks that call rather than reaching the network
    # mid-test. Characters bound tokens from above, not below, so this first
    # assertion is a size claim, not a token claim: 100,000+ characters of
    # description is what makes the token measurement below worth trusting
    # (it comes out at ~4.5 characters per token for this text).
    characters = measure_cost(catalog, len)
    assert characters["total"] > 100_000

    tiktoken = pytest.importorskip("tiktoken")
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
    except Exception as exc:  # pragma: no cover - depends on the host's cache
        pytest.skip(f"cl100k_base is not available offline: {exc}")
    measured = measure_cost(catalog, lambda text: len(encoding.encode(text)))
    assert measured["total"] > 10_000
    assert 40 < measured["per_entry"] < 120
