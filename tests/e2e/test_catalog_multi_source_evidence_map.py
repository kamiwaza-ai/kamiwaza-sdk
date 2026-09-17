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
from types import SimpleNamespace

import pytest

from tests.e2e import _evidence_emitter as emitter
from tests.integration import test_catalog_multi_source as t02_module

pytestmark = pytest.mark.unit

T02_MODULE = "tests/integration/test_catalog_multi_source.py"
REGISTRY = ("catalog.dataset-registry",)
RETRIEVAL = ("retrieval.async-retrieval-jobs",)
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


def test_every_t02_test_is_listed() -> None:
    defined = {
        name
        for name, member in inspect.getmembers(t02_module, inspect.isfunction)
        if name.startswith("test_") and member.__module__ == t02_module.__name__
    }

    assert defined == set(EXPECTED_CLAIMS), (
        f"unlisted: {sorted(defined - set(EXPECTED_CLAIMS))}; "
        f"listed but missing: {sorted(set(EXPECTED_CLAIMS) - defined)}"
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


@pytest.mark.parametrize("test_name", MAPPED_TESTS)
def test_t02_mapped_test_carries_no_outcome_changing_marker(test_name: str) -> None:
    found = sorted(
        mark.name for mark in _marks(test_name) if mark.name in OUTCOME_CHANGING_MARKERS
    )
    assert not found, f"{test_name} carries {found}; report failures as failures"
