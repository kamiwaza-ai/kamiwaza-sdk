"""Neutral credential-free read and model journey across attestation profiles."""

from __future__ import annotations

from pathlib import Path

import pytest

from kamiwaza_sdk.delegated_workloads import AttestationProfile

from .neutral_read_model_support import (
    EXPECTED_APPLICATION_STEPS,
    JourneyOutcome,
    MEMBER_ID,
    run_neutral_read_model_journey,
)

pytestmark = pytest.mark.e2e


def test_unchanged_neutral_client_passes_offline_and_tokenreview_profiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes = tuple(
        run_neutral_read_model_journey(profile, tmp_path, monkeypatch)
        for profile in AttestationProfile
    )

    offline, tokenreview = outcomes
    assert offline.profile is AttestationProfile.KUBERNETES_OFFLINE_V1
    assert tokenreview.profile is AttestationProfile.KUBERNETES_TOKENREVIEW_V1
    assert offline.application_steps == tokenreview.application_steps
    assert offline.workload_actor_id == tokenreview.workload_actor_id
    _assert_outcome(offline)
    _assert_outcome(tokenreview)


def _assert_outcome(outcome: JourneyOutcome) -> None:
    assert outcome.application_steps == EXPECTED_APPLICATION_STEPS
    assert outcome.member_subject_id == MEMBER_ID
    assert outcome.workload_actor_id != outcome.member_subject_id
    assert outcome.read_result == "neutral document"
    assert outcome.model_result == "neutral summary"
    assert outcome.attribution_is_correlated
