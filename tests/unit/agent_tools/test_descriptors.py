from __future__ import annotations

import re
import warnings

import pytest

from kamiwaza_sdk.agent_tools.descriptors import (
    APPROVAL_REQUIRED_READS,
    HINT_OVERRIDES,
    OPEN_WORLD_OPERATIONS,
    BehaviourHints,
    derive_hints,
    describe,
    describe_all,
    unknown_verbs,
)
from kamiwaza_sdk.agent_tools.spec_index import build_index

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def index():
    from kamiwaza_sdk.client import KamiwazaClient

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return build_index(KamiwazaClient(base_url="http://localhost:7777/api"))


def test_every_published_operation_derives_hints(index) -> None:
    """A verb no set knows must be a build failure, not a silent read-only.

    This is the test that makes derivation safe: the fallback gates everything,
    so a new verb cannot publish a mutation as a free call.
    """
    assert unknown_verbs(index) == ()


def test_an_unknown_verb_is_treated_as_a_mutation_not_a_read(index) -> None:
    """A new verb must stay reachable (FR-005c) and must not read as free.

    Raising here instead would take the whole catalog down over one method
    added upstream, which is a worse failure than describing it cautiously.
    """
    entry = index.published[0]
    odd = type(entry)(
        selector="mystery.frobnicate_thing",
        published_id="frobnicate_thing_mystery",
        service="mystery",
        method="frobnicate_thing",
        summary=None,
        parameters=(),
        required_parameters=(),
        returns=None,
    )
    assert derive_hints(odd.selector, odd.method) is None
    descriptor = describe(odd)
    assert descriptor.hints.destructive
    assert not descriptor.hints.read_only
    assert not descriptor.hints.idempotent
    assert descriptor.requires_approval


@pytest.mark.parametrize(
    ("method", "read_only", "destructive", "idempotent"),
    [
        ("list_models", True, False, True),
        ("get_model", True, False, True),
        ("create_agent", False, False, False),
        ("deploy_model", False, False, False),
        ("update_user", False, False, True),
        ("delete_user", False, True, True),
        ("revoke_pat", False, True, True),
    ],
)
def test_hints_come_from_the_leading_verb(
    method: str, read_only: bool, destructive: bool, idempotent: bool
) -> None:
    hints = derive_hints(f"svc.{method}", method)
    assert hints == BehaviourHints(
        read_only=read_only,
        destructive=destructive,
        idempotent=idempotent,
        open_world=False,
    )


def test_an_override_beats_the_verb_rule() -> None:
    """FR-006e: a wrong derivation is corrected per operation, not abandoned.

    "stop" derives as an ordinary change. For a deployment it ends something,
    so the override must make it destructive — and only for the two selectors
    named, leaving the same verb elsewhere alone.
    """
    assert HINT_OVERRIDES["serving.stop_deployment"].destructive
    derived = derive_hints("other.stop_deployment", "stop_deployment")
    assert derived is not None and not derived.destructive


def test_hint_override_replaces_the_derivation(index) -> None:
    entry = next(
        e for e in index if e.selector == "cluster.rotate_preshared_key"
    )
    override = HINT_OVERRIDES[entry.selector]
    assert override.idempotent is False, "a key returned once cannot be idempotent"
    derived = derive_hints(entry.selector, entry.method)
    assert derived is not None and derived.idempotent, (
        "the verb rule reads 'rotate' as a repeatable change, which is exactly "
        "the derivation this override exists to correct"
    )
    assert describe(entry).hints == override


def test_a_removal_is_never_published_as_a_free_read(index) -> None:
    """The property the tables exist to hold, asserted against the real index.

    Stated as a rule about the method name rather than a list of selectors:
    `admin` and `declare` sat in the read verb set once, which published
    `workrooms.admin_delete` — a `DELETE /admin/workrooms/{id}` that purges
    any workroom regardless of owner — as a read-only, approval-exempt call.
    Every assertion around this one compared a table against itself and so
    passed.
    """
    removal = re.compile(r"(?:^|_)(delete|purge|revoke|remove|destroy|drop)(?:_|$)")
    wrong = [
        d.entry.selector
        for d in describe_all(index)
        if removal.search(d.entry.method)
        and (d.hints.read_only or not d.requires_approval)
    ]
    assert wrong == [], (
        "operations whose name says they remove something, published as a "
        f"read or without approval: {wrong}"
    )


def test_open_world_defaults_to_false(index) -> None:
    """FR-006f: the allowlist is curated, so the default must be closed.

    Scoped to the published set: the allowlist also names operations on the
    deprecated `tools` service, which is withheld and so never described.
    """
    published = {e.selector for e in index if e.is_published}
    open_world = {d.entry.selector for d in describe_all(index) if d.hints.open_world}
    assert open_world == set(OPEN_WORLD_OPERATIONS) & published


def test_approval_covers_every_mutation_and_the_disclosing_reads(index) -> None:
    """FR-006g: 'not read-only, or named in the approval-required-reads set'.

    Asserted as two observable facts rather than by recomputing the rule, which
    is what let a wrong table ship green: nothing that mutates is free, and
    every named read is gated.
    """
    descriptors = describe_all(index)
    free_mutations = [
        d.entry.selector
        for d in descriptors
        if not d.hints.read_only and not d.requires_approval
    ]
    assert free_mutations == []
    gated = {d.entry.selector for d in descriptors if d.requires_approval}
    assert set(APPROVAL_REQUIRED_READS) <= gated




def test_approval_required_reads_are_read_only(index) -> None:
    """A mutation in that set would be redundant and hide a derivation bug."""
    by_selector = {d.selector: d for d in describe_all(index)}
    for selector in APPROVAL_REQUIRED_READS:
        assert by_selector[selector].hints.read_only


def test_every_override_names_a_real_operation(index) -> None:
    """A stale override is silent, so the test names it instead."""
    selectors = {entry.selector for entry in index}
    for name, configured in (
        ("HINT_OVERRIDES", set(HINT_OVERRIDES)),
        ("OPEN_WORLD_OPERATIONS", set(OPEN_WORLD_OPERATIONS)),
        ("APPROVAL_REQUIRED_READS", set(APPROVAL_REQUIRED_READS)),
    ):
        assert configured <= selectors, f"{name} names operations that do not exist"


def test_paging_is_detected_from_parameters(index) -> None:
    for descriptor in describe_all(index):
        has_paging = bool(
            {"page", "per_page", "limit", "offset", "cursor"}
            & set(descriptor.entry.parameters)
        )
        assert descriptor.paginated is has_paging


def test_unpublished_operations_are_not_described(index) -> None:
    described = {d.selector for d in describe_all(index)}
    withheld = {e.selector for e in index if not e.is_published}
    assert described & withheld == set()
