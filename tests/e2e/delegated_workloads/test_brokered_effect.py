"""End-to-end typed SDK journey for one exact brokered effect."""

from __future__ import annotations

from uuid import UUID

import pytest

from kamiwaza_sdk.delegated_workloads import (
    CredentialBindingUnavailable,
    CredentialUseStatus,
    EffectDigestConflict,
    InvalidRequest,
    ReplayRejected,
    TrustedAdapterLease,
)

from .brokered_effect_harness import (
    BINDING_ID,
    EFFECT_ID,
    BrokerJourneyHarness,
)

pytestmark = pytest.mark.e2e


def test_read_effect_redelivery_executes_the_provider_exactly_once() -> None:
    harness = BrokerJourneyHarness.create()
    effect_request, use_request = harness.read_requests("read:doc-7")

    first = harness.executor.reserve_effect(effect_request, harness.run_authority)
    redelivered = harness.executor.reserve_effect(
        effect_request, harness.run_authority
    )
    receipt = harness.broker.execute(
        TrustedAdapterLease.from_effect(first, use_request)
    )

    assert first.effect_id == redelivered.effect_id == UUID(EFFECT_ID)
    assert receipt.status is CredentialUseStatus.SUCCEEDED
    assert receipt.result == {
        "document": {"content": "original", "id": "doc-7"}
    }
    assert len(harness.provider.records) == 1
    assert harness.provider.records[0].operation_id == "fake.documents.get"
    with pytest.raises(ReplayRejected):
        harness.broker.execute(
            TrustedAdapterLease.from_effect(redelivered, use_request)
        )
    assert len(harness.provider.records) == 1


def test_binding_revocation_denies_before_provider_use_and_is_acknowledged() -> None:
    harness = BrokerJourneyHarness.create()
    effect_request, use_request = harness.read_requests("revoked:doc-7")
    effect = harness.executor.reserve_effect(
        effect_request,
        harness.run_authority,
    )

    harness.revoke_binding(UUID(BINDING_ID))

    with pytest.raises(CredentialBindingUnavailable):
        harness.broker.execute(
            TrustedAdapterLease.from_effect(
                effect,
                use_request,
            )
        )
    assert harness.provider.revocation_acknowledged
    assert harness.provider.records == ()


def test_provider_revocation_independently_denies_before_provider_use() -> None:
    harness = BrokerJourneyHarness.create()
    effect_request, use_request = harness.read_requests("provider-revoked:doc-7")
    effect = harness.executor.reserve_effect(
        effect_request,
        harness.run_authority,
    )
    harness.resource.revoke()

    with pytest.raises(CredentialBindingUnavailable):
        harness.broker.execute(
            TrustedAdapterLease.from_effect(effect, use_request)
        )

    assert harness.provider.records == ()


def test_changed_digest_under_a_redelivered_effect_key_denies() -> None:
    harness = BrokerJourneyHarness.create()
    effect_request, _use_request = harness.read_requests("changed:doc-7")
    harness.executor.reserve_effect(effect_request, harness.run_authority)
    changed = effect_request.model_copy(
        update={"effect_digest": "sha256:" + "e" * 64}
    )

    with pytest.raises(EffectDigestConflict):
        harness.executor.reserve_effect(changed, harness.run_authority)

    assert harness.provider.records == ()


@pytest.mark.parametrize(
    "changes",
    [
        {"credential_binding_id": UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")},
        {"request_digest": "sha256:" + "f" * 64},
        {"operation_id": "fake.documents.delete"},
    ],
)
def test_use_metadata_substitution_denies_before_provider_use(
    changes: dict[str, object],
) -> None:
    harness = BrokerJourneyHarness.create()
    effect_request, use_request = harness.read_requests("substitution:doc-7")
    effect = harness.executor.reserve_effect(effect_request, harness.run_authority)
    changed = use_request.model_copy(update=changes)

    with pytest.raises(InvalidRequest):
        harness.broker.execute(
            TrustedAdapterLease.from_effect(effect, changed)
        )

    assert harness.provider.records == ()


def test_lost_mutation_response_is_terminal_ambiguous_without_replay() -> None:
    harness = BrokerJourneyHarness.create()
    effect_request, use_request = harness.update_requests(
        "update:doc-7", "committed-before-response-loss"
    )
    effect = harness.executor.reserve_effect(
        effect_request,
        harness.run_authority,
    )
    lease = TrustedAdapterLease.from_effect(effect, use_request)
    harness.provider.lose_next_response()

    receipt = harness.broker.execute(lease)

    assert receipt.status is CredentialUseStatus.AMBIGUOUS
    assert receipt.result == {}
    assert harness.resource.document("doc-7")["content"] == (
        "committed-before-response-loss"
    )
    assert len(harness.provider.records) == 1
    assert harness.provider.records[0].outcome == "response_lost"
    with pytest.raises(ReplayRejected):
        harness.broker.execute(lease)
    assert len(harness.provider.records) == 1


def test_journey_observations_contain_only_safe_binding_and_lease_metadata() -> None:
    harness = BrokerJourneyHarness.create()
    effect_request, use_request = harness.read_requests("safe:doc-7")
    effect = harness.executor.reserve_effect(
        effect_request,
        harness.run_authority,
    )

    receipt = harness.broker.execute(
        TrustedAdapterLease.from_effect(
            effect,
            use_request,
        )
    )
    rendered = repr((receipt, harness.safe_events, harness.provider.records))

    assert "provider-access-canary" not in rendered
    assert "provider-revocation-canary" not in rendered
    assert all(event.binding_id == UUID(BINDING_ID) for event in harness.safe_events)
