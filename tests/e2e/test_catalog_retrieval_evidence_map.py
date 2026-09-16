"""Evidence-map guard for T01, the catalog ingest-to-retrieval round trip.

The T01 record claims ``retrieval.async-retrieval-jobs``, whose capability
document requires Arrow Flight retrieval as well as inline retrieval. If the
gRPC test stopped feeding the record, a passing inline run alone would
characterize a capability it does not establish. The shipped map must
therefore route BOTH T01 tests into ONE entry and exclude neither.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from tests.e2e import _evidence_emitter as emitter
from tests.integration import test_catalog_ingest_retrieval as t01_module

pytestmark = pytest.mark.unit

T01_MODULE = "tests/integration/test_catalog_ingest_retrieval.py"
T01_TESTS = ("test_s3_ingest_and_retrieve_inline", "test_s3_ingest_and_retrieve_grpc")
T01_CAPABILITIES = ("catalog.dataset-registry", "retrieval.async-retrieval-jobs")


def _collected_item(test_name: str) -> SimpleNamespace:
    """Stand in for a collected pytest item: nodeid plus the test's real markers.

    Resolving the function by name fails loudly if a T01 test is renamed, and
    carrying its actual markers keeps ``marker:`` map patterns honest.
    """
    test_fn = getattr(t01_module, test_name)
    marks = [*t01_module.pytestmark, *getattr(test_fn, "pytestmark", [])]

    def get_closest_marker(name: str) -> object | None:
        return next((mark for mark in marks if mark.name == name), None)

    return SimpleNamespace(
        nodeid=f"{T01_MODULE}::{test_name}",
        get_closest_marker=get_closest_marker,
    )


def test_t01_entry_maps_both_transports_and_excludes_neither() -> None:
    entries = emitter.load_capability_map(emitter.DEFAULT_MAP_PATH)
    items = [_collected_item(name) for name in T01_TESTS]

    matching = [
        entry for entry in entries if any(entry.matches(item) for item in items)
    ]

    assert len(matching) == 1, (
        "T01 tests must feed exactly one capability_map entry; found "
        f"{[entry.scenario_name for entry in matching]}"
    )
    (entry,) = matching
    unmatched = [item.nodeid for item in items if not entry.matches(item)]
    assert not unmatched, f"T01 entry {entry.scenario_name!r} drops {unmatched}"
    assert (
        entry.capability_ids == T01_CAPABILITIES
    ), f"T01 entry claims {entry.capability_ids}, expected {T01_CAPABILITIES}"
