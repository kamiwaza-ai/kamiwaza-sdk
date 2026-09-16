from __future__ import annotations

import warnings

import pytest

from kamiwaza_sdk.agent_tools.descriptors import (
    APPROVAL_REQUIRED_READS,
    EFFECT_OVERRIDES,
    HINT_OVERRIDES,
    OPEN_WORLD_OPERATIONS,
    BehaviourHints,
    Effect,
    classify,
    describe,
    describe_all,
    unclassified,
)
from kamiwaza_sdk.agent_tools.spec_index import build_index

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def index():
    from kamiwaza_sdk.client import KamiwazaClient

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return build_index(KamiwazaClient(base_url="http://localhost:7777/api"))


def test_every_published_operation_classifies(index) -> None:
    """A verb no rule knows must be a build failure, not a silent read-only.

    This is the test that makes derivation safe: the fallback is refusal, so a
    new verb cannot publish a mutation as a free call.
    """
    assert unclassified(index) == ()


def test_an_unknown_verb_refuses_rather_than_defaulting(index) -> None:
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
    assert classify(odd.selector, odd.method) is None
    with pytest.raises(ValueError, match="unclassified verb"):
        describe(odd)


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("list_models", Effect.READ),
        ("get_model", Effect.READ),
        ("create_agent", Effect.CREATE),
        ("deploy_model", Effect.CREATE),
        ("update_user", Effect.UPDATE),
        ("delete_user", Effect.DESTROY),
        ("revoke_pat", Effect.DESTROY),
    ],
)
def test_effect_comes_from_the_leading_verb(method: str, expected: Effect) -> None:
    assert classify(f"svc.{method}", method) is expected


def test_an_override_beats_the_verb_rule() -> None:
    """FR-006e: a wrong derivation is corrected per operation, not abandoned."""
    assert classify("serving.stop_deployment", "stop_deployment") is Effect.DESTROY
    assert classify("other.stop_deployment", "stop_deployment") is Effect.UPDATE


def test_hints_follow_the_effect(index) -> None:
    for descriptor in describe_all(index):
        if descriptor.entry.selector in HINT_OVERRIDES:
            continue
        assert descriptor.hints.read_only is descriptor.effect.is_read
        assert descriptor.hints.destructive is (descriptor.effect is Effect.DESTROY)
        assert descriptor.hints.idempotent is (descriptor.effect is not Effect.CREATE)


def test_hint_override_replaces_the_derivation(index) -> None:
    entry = next(
        e for e in index if e.selector == "cluster.rotate_preshared_key"
    )
    override = HINT_OVERRIDES[entry.selector]
    assert override.idempotent is False, "a key returned once cannot be idempotent"
    derived_effect = classify(entry.selector, entry.method)
    assert derived_effect is Effect.UPDATE
    assert override != BehaviourHints(
        read_only=False, destructive=False, idempotent=True, open_world=False
    )


def test_open_world_defaults_to_false(index) -> None:
    """FR-006f: the allowlist is curated, so the default must be closed."""
    for descriptor in describe_all(index):
        expected = descriptor.entry.selector in OPEN_WORLD_OPERATIONS
        assert descriptor.hints.open_world is expected


def test_approval_is_every_mutation_plus_the_named_reads(index) -> None:
    """FR-006g: 'not read-only, or named in the approval-required-reads set'."""
    for descriptor in describe_all(index):
        expected = (
            not descriptor.hints.read_only
            or descriptor.entry.selector in APPROVAL_REQUIRED_READS
        )
        assert descriptor.requires_approval is expected


def test_no_mutation_escapes_approval(index) -> None:
    unapproved = [
        d.selector
        for d in describe_all(index)
        if not d.hints.read_only and not d.requires_approval
    ]
    assert unapproved == []


def test_approval_required_reads_are_read_only(index) -> None:
    """A mutation in that set would be redundant and hide a classification bug."""
    by_selector = {d.selector: d for d in describe_all(index)}
    for selector in APPROVAL_REQUIRED_READS:
        assert by_selector[selector].hints.read_only


def test_every_override_names_a_real_operation(index) -> None:
    """A stale override is silent, so the test names it instead."""
    selectors = {entry.selector for entry in index}
    for name, configured in (
        ("EFFECT_OVERRIDES", set(EFFECT_OVERRIDES)),
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
