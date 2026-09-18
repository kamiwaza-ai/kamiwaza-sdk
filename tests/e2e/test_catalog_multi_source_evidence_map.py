"""Evidence-map guard for T02, the multi-source catalog tests.

An evidence record is composed from whichever mapped tests actually ran, so a
claim is honest only when each test earns it on its own (the PER-TEST RULE in
capability_map.yaml). T02 splits the module into a registry entry and a
retrieval entry, so a retrieval failure cannot fail the registry record. Both
entries match every test in the module unless their exclude lists name it, so a
new or renamed test would silently feed both claims. This guard names the claims
each test feeds and fails when the module has a test it does not name.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from tests.e2e import _evidence_emitter as emitter
from tests.integration import test_catalog_multi_source as t02_module

pytestmark = pytest.mark.unit

T02_MODULE = "tests/integration/test_catalog_multi_source.py"
REGISTRY = ("catalog.dataset-registry",)
RETRIEVAL = ("retrieval.async-retrieval-jobs",)
# The scenario_name of each entry, in map order, keyed by the claims it makes: the
# emitter derives the record's scenario_id from it, so renaming one silently writes
# a different record.
SCENARIO_NAMES: dict[tuple[str, ...], str] = {
    REGISTRY: "Multi-source catalog ingestion",
    RETRIEVAL: "Multi-source inline and SSE retrieval",
}
# The claims of every entry a test matches, in map order. The optional paths
# (file, Kafka, Slack, oversized object) feed none.
EXPECTED_CLAIMS: dict[str, tuple[tuple[str, ...], ...]] = {
    "test_catalog_object_ingestion_metadata": (REGISTRY,),
    "test_catalog_parquet_ingestion_metadata": (REGISTRY,),
    "test_catalog_postgres_ingestion_metadata": (REGISTRY,),
    "test_catalog_container_link_sets_dataset_container_urn": (REGISTRY,),
    "test_catalog_object_ingestion_inline_retrieval": (RETRIEVAL,),
    "test_catalog_parquet_ingestion_inline_retrieval": (RETRIEVAL,),
    "test_catalog_inline_small_object_succeeds": (RETRIEVAL,),
    "test_catalog_postgres_inline_retrieval": (RETRIEVAL,),
    "test_catalog_sse_retrieval_emits_terminal_event": (RETRIEVAL,),
    "test_catalog_file_ingestion_metadata": (),
    "test_catalog_kafka_ingestion_metadata": (),
    "test_catalog_slack_ingestion_metadata": (),
    "test_catalog_inline_large_object_hits_threshold": (),
}
MAPPED_TESTS = sorted(name for name, claims in EXPECTED_CLAIMS.items() if claims)
# A skipped or xfailed step never evidences anything (the emitter records an
# xfail as skipped), so a known product failure must stay a plain failure.
# Only decorator markers are checked here; a runtime pytest.skip or
# pytest.xfail inside a test body is not detected.
OUTCOME_CHANGING_MARKERS = frozenset({"skip", "skipif", "xfail"})


def _marks(test_name: str) -> list[pytest.Mark]:
    """The test's real markers; resolving it by name fails loudly on a rename."""
    test_fn = getattr(t02_module, test_name)
    module_marks = [decorator.mark for decorator in t02_module.pytestmark]
    return [*module_marks, *getattr(test_fn, "pytestmark", [])]


def _matching_entries(test_name: str) -> list[emitter.MapEntry]:
    marks = _marks(test_name)

    def get_closest_marker(name: str) -> pytest.Mark | None:
        return next((mark for mark in marks if mark.name == name), None)

    item = SimpleNamespace(
        nodeid=f"{T02_MODULE}::{test_name}", get_closest_marker=get_closest_marker
    )
    entries = emitter.load_capability_map(emitter.DEFAULT_MAP_PATH)
    return [entry for entry in entries if entry.matches(item)]


def _defined_in_module(predicate: Callable[[object], bool], prefix: str) -> set[str]:
    """Names pytest would collect from this module, wherever they were defined.

    A test imported into the module (``from other import test_x``) collects under
    this module's nodeid and feeds these entries, so it is not filtered out by
    ``__module__``.
    """
    return {
        name
        for name, _member in inspect.getmembers(t02_module, predicate)
        if name.startswith(prefix)
    }


def test_every_t02_test_is_listed() -> None:
    # "test", not "test_": pytest's default python_functions is test*, so a
    # function named testcatalog_x would collect and feed both entries.
    defined = _defined_in_module(inspect.isfunction, "test")

    assert defined == set(EXPECTED_CLAIMS), (
        f"unlisted: {sorted(defined - set(EXPECTED_CLAIMS))}; "
        f"listed but missing: {sorted(set(EXPECTED_CLAIMS) - defined)}"
    )


def test_t02_module_defines_no_test_classes() -> None:
    """A test in a class would escape the listing above and feed both entries.

    ``EXPECTED_CLAIMS`` is keyed by module-level function name, and both map entries
    match any nodeid under this module that their exclude lists do not name, so
    ``TestX::test_y`` would feed the registry and the retrieval record at once.
    """
    classes = _defined_in_module(inspect.isclass, "Test")

    assert not classes, (
        f"{sorted(classes)} would collect as nodeids this guard does not check; "
        "keep T02 tests as module-level functions, or extend EXPECTED_CLAIMS"
    )


@pytest.mark.parametrize("test_name", sorted(EXPECTED_CLAIMS))
def test_t02_test_feeds_exactly_its_expected_claims(test_name: str) -> None:
    matching = _matching_entries(test_name)

    assert (
        tuple(entry.capability_ids for entry in matching) == EXPECTED_CLAIMS[test_name]
    ), (
        f"{test_name} must feed {EXPECTED_CLAIMS[test_name]}; found "
        f"{[(entry.scenario_name, entry.capability_ids) for entry in matching]}"
    )
    for entry in matching:
        assert entry.evidence_provenance == "cycle-authored", (
            f"{entry.scenario_name!r} is stamped {entry.evidence_provenance!r}; "
            "the T02 assertions were written in-cycle to evidence the capability"
        )
        assert entry.scenario_name == SCENARIO_NAMES[entry.capability_ids], (
            f"{entry.capability_ids} is named {entry.scenario_name!r}; the emitter "
            "derives the record's scenario_id from this name"
        )


@pytest.mark.parametrize("test_name", MAPPED_TESTS)
def test_t02_mapped_test_carries_no_outcome_changing_marker(test_name: str) -> None:
    found = sorted(
        mark.name for mark in _marks(test_name) if mark.name in OUTCOME_CHANGING_MARKERS
    )
    assert not found, f"{test_name} carries {found}; report failures as failures"
