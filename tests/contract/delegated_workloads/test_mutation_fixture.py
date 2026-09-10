"""Exact-approved neutral mutation and unknown-result fixture contracts."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from examples.delegated_resource_server.app import DocumentStore
from examples.delegated_resource_server.mutations import (
    ExactApprovedMutationFixture,
    MutationAuthorityRejected,
    MutationOutcomeUnknown,
    MutationReplayRejected,
    MutationRequest,
)
from kamiwaza_sdk.delegated_workloads import ResourceRef, SealedDelegatedContext
from tests.unit.delegated_workloads.resource_guard_support import guard_case


pytestmark = pytest.mark.contract
_DIGEST = "sha256:" + "d" * 64


def test_exact_consumed_context_executes_one_content_minimized_mutation() -> None:
    store = DocumentStore()
    fixture = ExactApprovedMutationFixture(store)
    context = _context()

    result = fixture.mutate(_request("Approved title"), context)

    assert result["title"] == "Approved title"
    assert result["version"] == 1
    assert fixture.records[0].effect_id == context.context.effect_id
    assert fixture.records[0].request_digest == _DIGEST
    assert fixture.records[0].outcome == "succeeded"
    assert "Approved title" not in str(asdict(fixture.records[0]))


def test_lost_response_commits_once_then_becomes_terminally_ambiguous() -> None:
    store = DocumentStore()
    fixture = ExactApprovedMutationFixture(store)
    context = _context()
    fixture.lose_next_response()

    with pytest.raises(MutationOutcomeUnknown, match="outcome is unknown"):
        fixture.mutate(_request("Committed before loss"), context)

    document = store.get("document:doc-7")
    assert document is not None
    assert document["title"] == "Committed before loss"
    assert fixture.records[0].outcome == "ambiguous"
    with pytest.raises(MutationReplayRejected, match="cannot be replayed"):
        fixture.mutate(_request("Second execution"), context)
    assert len(fixture.records) == 1
    assert store.get("document:doc-7") == {
        "id": "document:doc-7",
        "status": "ready",
        "title": "Committed before loss",
        "version": 1,
    }


@pytest.mark.parametrize(
    ("action", "resource_id"),
    [
        ("read", "document:doc-7"),
        ("mutate", "document:doc-8"),
    ],
)
def test_unsealed_action_or_resource_authority_is_rejected(
    action: str,
    resource_id: str,
) -> None:
    store = DocumentStore()
    fixture = ExactApprovedMutationFixture(store)
    context = _context(action=action, resource_id=resource_id)

    with pytest.raises(MutationAuthorityRejected, match="authority is unavailable"):
        fixture.mutate(_request("Denied"), context)

    assert store.get("document:doc-7") is None
    assert fixture.records == ()


@pytest.mark.parametrize(
    "mutation_request",
    [
        MutationRequest("document:doc-7", "", _DIGEST),
        MutationRequest("document:doc-7", "title", "not-a-digest"),
    ],
)
def test_mutation_request_requires_exact_safe_inputs(
    mutation_request: MutationRequest,
) -> None:
    with pytest.raises(ValueError, match="mutation request is invalid"):
        ExactApprovedMutationFixture(DocumentStore()).mutate(
            mutation_request,
            _context(),
        )


def _request(title: str) -> MutationRequest:
    return MutationRequest("document:doc-7", title, _DIGEST)


def _context(
    *,
    action: str = "mutate",
    resource_id: str = "document:doc-7",
) -> SealedDelegatedContext:
    context = guard_case().context.model_copy(
        update={
            "action": action,
            "resource": ResourceRef(
                type="conformance.document",
                descriptor_version="v1",
                id=resource_id,
            ),
        }
    )
    return SealedDelegatedContext._verified(context)
