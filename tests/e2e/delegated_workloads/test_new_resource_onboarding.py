"""Neutral new-resource onboarding through the public delegated contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from .new_resource_support import (
    EXPECTED_STEPS,
    MEMBER_SUBJECT_ID,
    run_new_resource_onboarding_journey,
)

pytestmark = pytest.mark.e2e


def test_neutral_resource_completes_exact_approved_guarded_journey(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome = run_new_resource_onboarding_journey(tmp_path, monkeypatch)

    assert outcome.steps == EXPECTED_STEPS
    assert outcome.registration_status == "active"
    assert outcome.pending_mutation_had_no_capability
    assert outcome.approved_effect_id == outcome.mutation_effect_id
    assert outcome.document_title == "Quarterly plan"
    assert outcome.document_version == 1
    assert outcome.member_subject_id == MEMBER_SUBJECT_ID
    assert outcome.workload_actor_id != outcome.member_subject_id
    assert outcome.guard_consumptions == 2
    assert outcome.replay_status == 403
