"""The platform-wide admin listing, and the names a run generates.

An absence check and its control come from one traversal, because two lookups
are two responses. Paging is capped, and the defaults match the SDK's own
documented ceiling rather than restating a number.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from tests.integration import _workroom_support as support
from tests.unit.workrooms._ledger_fakes import (
    rooms,
)

pytestmark = pytest.mark.unit


class AdminListing:
    """The administrator listing, one fixed page per offset."""

    def __init__(self, pages: dict[int, list]) -> None:
        self.pages = pages
        self.skips: list[int] = []
        self.include_deleted: list[bool] = []

    def admin_list(self, *, include_deleted: bool, skip: int, limit: int):
        self.skips.append(skip)
        self.include_deleted.append(include_deleted)
        return self.pages.get(skip, [])


def test_find_admin_workroom_pages_until_it_finds_the_workroom() -> None:
    listing = AdminListing({0: rooms("a", "b"), 2: rooms("c", "target")})
    admin = SimpleNamespace(workrooms=listing)

    found = support.find_admin_workroom(
        admin, "target", include_deleted=True, page_size=2
    )

    assert found is not None and found.id == "target"
    assert listing.skips == [0, 2]
    assert listing.include_deleted == [True, True]


def test_find_admin_workroom_answers_none_at_the_first_short_page() -> None:
    listing = AdminListing({0: rooms("a", "b"), 2: rooms("c")})
    admin = SimpleNamespace(workrooms=listing)

    assert (
        support.find_admin_workroom(admin, "target", include_deleted=False, page_size=2)
        is None
    )
    assert listing.skips == [0, 2]
    assert listing.include_deleted == [False, False]


def test_find_admin_workroom_stops_at_its_page_cap() -> None:
    listing = AdminListing({skip: rooms("x", "y") for skip in range(0, 20, 2)})
    admin = SimpleNamespace(workrooms=listing)

    with pytest.raises(AssertionError, match="did not end within 3 pages"):
        support.find_admin_workroom(
            admin, "target", include_deleted=False, page_size=2, max_pages=3
        )
    assert listing.skips == [0, 2, 4]


def test_admin_workroom_ids_returns_every_id_one_traversal_saw() -> None:
    """The absence check and its control both come out of this set."""
    listing = AdminListing({0: rooms("a", "b"), 2: rooms("c")})

    seen = support.admin_workroom_ids(
        SimpleNamespace(workrooms=listing), include_deleted=False, page_size=2
    )

    assert seen == {"a", "b", "c"}, "the traversal dropped rows"
    assert listing.skips == [0, 2], "the pages were not walked in order"


def test_admin_workroom_ids_forwards_include_deleted() -> None:
    listing = AdminListing({0: rooms("a")})

    support.admin_workroom_ids(
        SimpleNamespace(workrooms=listing), include_deleted=True, page_size=2
    )

    assert listing.include_deleted == [True]


def test_admin_workroom_ids_stops_at_its_page_cap() -> None:
    """A listing that never ends must fail loudly, not answer a partial set."""
    listing = AdminListing({skip: rooms("a", "b") for skip in range(0, 20, 2)})

    with pytest.raises(AssertionError, match="did not end within"):
        support.admin_workroom_ids(
            SimpleNamespace(workrooms=listing),
            include_deleted=False,
            page_size=2,
            max_pages=3,
        )


def test_the_admin_paging_defaults_match_the_sdks_own_ceiling() -> None:
    """1000 is the SDK's validated maximum; a smaller default widens the window."""
    assert support.ADMIN_PAGE_SIZE == 1000
    assert support.ADMIN_MAX_PAGES == 50


def test_expected_dataset_urn_matches_the_v121_scheme() -> None:
    """The scheme every live run re-checks against the URN the server answers."""
    assert (
        support.expected_dataset_urn("sdk-evidence-x")
        == "urn:li:dataset:(urn:li:dataPlatform:s3,sdk-evidence-x,PROD)"
    )


def test_dataset_payload_names_an_s3_dataset_under_the_evidence_prefix() -> None:
    payload = support.dataset_payload("sdk-evidence-x-1")

    assert (payload.name, payload.platform) == ("sdk-evidence-x-1", "s3")
    assert payload.properties == {"path": "s3://sdk-evidence/sdk-evidence-x-1.json"}


def test_every_generated_name_is_unique_to_this_run() -> None:
    """Cleanup can delete by name, so two runs must not generate the same one."""
    names = {support.unique_name("collide") for _ in range(2000)}

    assert len(names) == 2000
    # A literal, not the constant: comparing the generated suffix to the
    # constant that generated it would stay green if the constant shrank.
    assert support.NAME_SUFFIX_HEX == 16, "64 bits is what the safety argument rests on"
    suffixes = {name.rsplit("-", 1)[1] for name in names}
    assert all(len(suffix) == 16 for suffix in suffixes)


def test_dataset_urns_forwards_its_query() -> None:
    """The bound-view listings are per-name; dropping the query changes what they see."""
    seen: list[str | None] = []

    class _Catalog:
        def list_datasets(self, query=None):
            seen.append(query)
            return [SimpleNamespace(urn="urn:one")]

    urns = support.dataset_urns(SimpleNamespace(catalog=_Catalog()), "only-this-name")

    assert urns == {"urn:one"}
    assert seen == ["only-this-name"]
