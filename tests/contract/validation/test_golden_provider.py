"""Golden provider contract and deterministic resolution tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kamiwaza_sdk.validation import RuntimeContext, ValidationProfile
from kamiwaza_sdk.validation.golden_provider import GoldenProvider
from kamiwaza_sdk.validation.provider import ProviderContractError
from kamiwaza_sdk.validation.testkit import RecordingFixtureStateWriter

from .support import profile_payload

pytestmark = pytest.mark.contract


def _golden_profile() -> ValidationProfile:
    payload = profile_payload()
    payload["validation"]["include"] = ["sdk.golden.echo/v1"]  # type: ignore[index]
    return ValidationProfile.model_validate(payload)


def test_golden_provider_describe_and_resolve_are_deterministic() -> None:
    provider = GoldenProvider()
    profile = _golden_profile()

    assert provider.describe() == provider.describe()
    assert provider.resolve(profile) == provider.resolve(profile)
    assert provider.resolve(profile).selected[0].case_ids == ("echo",)


def test_golden_provider_rejects_unknown_requested_scenario() -> None:
    payload = profile_payload()
    payload["validation"]["include"] = ["unknown.scenario/v1"]  # type: ignore[index]

    with pytest.raises(ProviderContractError, match="unknown requested scenario"):
        GoldenProvider().resolve(ValidationProfile.model_validate(payload))


def test_golden_provider_rejects_required_inapplicable_target() -> None:
    payload = profile_payload()
    payload["validation"]["include"] = ["sdk.golden.echo/v1"]  # type: ignore[index]
    payload["inference_targets"][0]["engine"] = "vllm"  # type: ignore[index]

    with pytest.raises(ProviderContractError, match="inapplicable required target"):
        GoldenProvider().resolve(ValidationProfile.model_validate(payload))


def test_golden_provider_rejects_requested_scenario_with_zero_targets() -> None:
    payload = profile_payload()
    payload["validation"]["include"] = ["sdk.golden.echo/v1"]  # type: ignore[index]
    payload["inference_targets"] = []

    with pytest.raises(ProviderContractError, match="zero selected cases"):
        GoldenProvider().resolve(ValidationProfile.model_validate(payload))


def test_golden_provider_rejects_include_exclude_deselection() -> None:
    payload = profile_payload()
    payload["validation"]["include"] = ["sdk.golden.echo/v1"]  # type: ignore[index]
    payload["validation"]["exclude"] = ["sdk.golden.echo/v1"]  # type: ignore[index]

    with pytest.raises(ValidationError, match="include and exclude overlap"):
        ValidationProfile.model_validate(payload)


def test_golden_provider_rejects_explicit_deselection() -> None:
    payload = profile_payload()
    payload["validation"]["include"] = []  # type: ignore[index]
    payload["validation"]["exclude"] = ["sdk.golden.echo/v1"]  # type: ignore[index]

    with pytest.raises(ProviderContractError, match="explicitly excluded"):
        GoldenProvider().resolve(ValidationProfile.model_validate(payload))


def test_golden_provider_rejects_changed_plan_revision_during_run() -> None:
    provider = GoldenProvider()
    plan = provider.resolve(_golden_profile())
    runtime = RuntimeContext.model_validate(
        {
            "schema": "kamiwaza.runtime-context/v1",
            "run_id": "revision-check",
            "clusters": [
                {
                    "id": "evo-x2-2",
                    "base_url": "https://evo-x2-2.example.test/api",
                    "api_key_ref": "secret://evo-x2-2/admin-pat",
                    "kubeconfig_ref": "file:///run/secrets/evo-x2-2.kubeconfig",
                }
            ],
        }
    )
    state = provider.prepare(plan, runtime, RecordingFixtureStateWriter())
    changed_plan = plan.model_copy(update={"provider_revision": "changed@v1"})

    with pytest.raises(ProviderContractError, match="provider revision mismatch"):
        provider.run(changed_plan, runtime, state)
