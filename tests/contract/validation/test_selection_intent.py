"""Explicit selection intent fails closed through the production adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kamiwaza_sdk.validation.cli import provider_main
from kamiwaza_sdk.validation.golden_provider import GoldenProvider

from .support import profile_payload

pytestmark = pytest.mark.contract


class RequestedScenarioOmittingProvider(GoldenProvider):
    def describe(self):  # type: ignore[no-untyped-def]
        descriptor = super().describe()[0]
        return (
            descriptor.model_copy(
                update={
                    "scenario_id": "sdk.unrelated.scenario/v1",
                    "minimum_level": "comprehensive",
                }
            ),
        )

    @staticmethod
    def _validate_requested_scenarios(profile):  # type: ignore[no-untyped-def]
        return None


def test_resolve_rejects_requested_scenario_missing_from_describe(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile_path = tmp_path / "profile.json"
    plan_path = tmp_path / "plan.json"
    profile_path.write_text(json.dumps(profile_payload()), encoding="utf-8")

    exit_code = provider_main(
        RequestedScenarioOmittingProvider(),
        ["resolve", "--profile", str(profile_path), "--plan", str(plan_path)],
    )

    assert exit_code == 2
    assert (
        "requested scenario is absent from descriptor catalog"
        in capsys.readouterr().err
    )
    assert not plan_path.exists()
