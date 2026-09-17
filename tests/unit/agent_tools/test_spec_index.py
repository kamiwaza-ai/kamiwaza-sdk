from __future__ import annotations

import re
import warnings

import pytest

from kamiwaza_sdk.agent_tools.ids import UNPUBLISHED, UnpublishedOperationError
from kamiwaza_sdk.agent_tools.spec_index import (
    DESCRIPTOR_VERSION,
    _CALLER_VOCABULARY,
    _NOISE_TERMS,
    build_index,
    meaningful_terms,
)
from tests._agent_tools_reachability import reachable_selectors

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



def test_index_covers_every_method_the_client_can_reach(client, index) -> None:
    """SC-029: coverage is asserted against the client, not against a number.

    A hardcoded count would pass while the index silently missed a whole
    family of operations — which is exactly what happened before the index
    walked nested sub-clients, hiding 38 callable operations including
    ``gates.packages.install``.
    """
    assert {entry.selector for entry in index} == reachable_selectors(client)


def test_local_helpers_are_not_indexed(client, index) -> None:
    """A nested object with no platform client of its own is not an operation.

    ``models.quant_manager`` computes over values already in hand and calls
    nothing, so publishing its five methods would offer an agent operations
    that reach no platform.
    """
    assert not any(".quant_manager." in entry.selector for entry in index)


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


def test_every_word_narrows_the_search_rather_than_widening_it(index) -> None:
    """The defect this scoring exists to prevent, pinned as a property.

    Measured before the fix: "deploy a model" matched 325 of 332 published
    operations, because any single term counted and something in the platform
    mentions "model" nearly everywhere. A search that gets worse the more
    precisely it is described is worse than no search, because an agent reads
    the first few results and concludes those are the options.
    """
    everything = len(index.published)
    broad = index.search("model", limit=everything)
    narrow = index.search("deploy a model", limit=everything)
    assert len(narrow) < len(broad), (
        "adding words widened the result set, which means a term is being "
        "counted as a match on its own again"
    )
    assert len(narrow) < everything // 4
    assert any("deploy" in e.published_id for e in narrow[:3])


def test_a_noise_length_term_is_ignored_beside_a_real_one(index) -> None:
    """"a" and "it" carry no signal as substrings, so they must not score.

    Dropped only when a longer term survives: a caller searching "id" alone
    still means it.
    """
    assert index.search("deploy a model") == index.search("deploy model")
    assert index.search("id", limit=5), "a short query on its own still searches"


def test_a_term_matches_at_a_word_start_not_inside_a_word(index) -> None:
    """Prefix matching is what a searcher expects; substring matching is not.

    "deploy" should still find "deployment"; nothing should match a term that
    merely appears inside an unrelated word.
    """
    found = {e.published_id for e in index.search("deploy", limit=len(index.published))}
    assert any("deployment" in name or "deploy" in name for name in found)
    for entry in index.search("port", limit=len(index.published)):
        text = f"{entry.published_id} {entry.selector} {entry.summary or ''}".lower()
        assert re.search(r"\bport", text), (
            f"{entry.published_id} matched 'port' inside a word such as "
            f"'transport' or 'important', which is not a match a caller meant"
        )


def test_the_requirement_widens_only_when_nothing_matches_everything(index) -> None:
    """A phrase nothing satisfies completely still answers with its best near miss.

    Empty is reserved for a query no operation matches at all: an agent that
    gets nothing back concludes the capability does not exist, so nothing is
    the wrong answer whenever a partial match exists (FR-040 in the server
    repository).
    """
    partial = index.search("ingest a dataset and index it", limit=len(index.published))
    assert partial, "a four-word phrase with real platform nouns returned nothing"
    assert index.search("zzzqqq wibblefrotz") == ()


def test_the_action_the_caller_named_outranks_a_shorter_reader(index) -> None:
    """A caller who says "stop" wants the stopping operation, not the getter.

    Measured before the leading-term signal existed: "stop deployment"
    returned three operations led by `get_deployment_apps`, because equal
    scores broke on the shorter identifier and every generic reader is
    shorter than the verb asked for.
    """
    assert index.search("stop deployment")[0].published_id.startswith("stop")
    assert index.search("delete user")[0].published_id == "delete_user_auth"


def test_a_relaxed_search_says_how_many_terms_it_required(index) -> None:
    """An unknown word answers on the rest, and the answer admits it.

    "wibble" names no operation and is no member's word for one, so the
    search falls back to "deployment" alone. That is still an answer and not
    a nothing-found (FR-040), but it reads exactly like a precise hit unless
    the ranking reports the level it answered at, which is what a surface
    labels the results with.
    """
    relaxed = index.search_ranking("wibble deployment")
    assert relaxed.entries, "a known platform noun with an unknown verb returned nothing"
    assert relaxed.terms == ("wibble", "deployment")
    assert relaxed.required_terms == 1
    assert relaxed.relaxed
    assert relaxed.matched_count >= len(relaxed.entries)
    assert all(
        "deployment" in f"{e.published_id} {e.summary or ''}".lower() for e in relaxed.entries
    )
    assert index.search_ranking("remove user").relaxed


def test_a_members_verb_reaches_the_operation_the_platform_named(index) -> None:
    """The words members used, ranked against the verbs the client uses.

    Measured before the vocabulary existed: each of these dropped the verb it
    could not match, searched the bare noun, and led with a generic reader —
    "shut down a deployment" answered with `get_deployment_apps`.
    """
    for query in ("shut down a deployment", "turn off a deployment to free up GPU"):
        assert index.search(query)[0].published_id.startswith("stop"), query
    assert index.search("make a new workspace for the security team")[0].published_id == (
        "create_workrooms"
    )
    assert index.search("add a new user to the platform")[0].published_id == (
        "create_local_user_auth"
    )


def test_an_expanded_term_counts_as_the_term_the_caller_gave(index) -> None:
    """Expansion narrows the search; it does not fake a relaxation.

    "shut down a deployment" matches every term it was given once "shut" and
    "down" carry "stop", so the surface must report it as a precise hit — and
    the platform's own wording must be untouched by the table.
    """
    shut = index.search_ranking("shut down a deployment")
    assert shut.terms == ("shut", "down", "deployment")
    assert shut.required_terms == 3
    assert not shut.relaxed
    assert index.search("stop deployment")[0].published_id.startswith("stop")
    assert any("add" in e.published_id for e in index.search("add publisher", limit=5))


def test_every_vocabulary_entry_maps_onto_a_published_operation(index) -> None:
    """An entry that no operation name carries is dead weight, so it must fail.

    This is the rule the table is written under: it maps towards words this
    client actually uses. Nothing else keeps it honest as the client changes.
    """
    names = " ".join(f"{e.published_id} {e.selector}" for e in index.published).lower()
    dead = {
        form
        for forms in _CALLER_VOCABULARY.values()
        for form in forms
        if form not in names
    }
    assert not dead, f"vocabulary maps onto nothing published: {sorted(dead)}"
    overlap = set(_CALLER_VOCABULARY) & _NOISE_TERMS
    assert not overlap, f"a term cannot be both expanded and dropped: {sorted(overlap)}"


def test_a_query_of_nothing_but_function_words_still_searches(index) -> None:
    """Dropping every term would match everything, which is worse than noise."""
    assert index.search("the", limit=5)
    assert meaningful_terms("make a new workspace for the security team") == [
        "make",
        "new",
        "workspace",
        "security",
        "team",
    ]


def test_a_precise_query_reports_no_relaxation(index) -> None:
    precise = index.search_ranking("stop deployment")
    assert precise.required_terms == len(precise.terms) == 2
    assert not precise.relaxed
    assert precise.matched_count == len(precise.entries)
    empty = index.search_ranking("   ")
    assert empty.entries == () and empty.terms == () and empty.required_terms == 0
    assert not empty.relaxed


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
