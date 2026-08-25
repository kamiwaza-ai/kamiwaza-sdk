"""Reusable provider test kit exercised by the golden provider."""

from __future__ import annotations

import pytest

from kamiwaza_sdk.validation import RuntimeContext, ValidationProfile
from kamiwaza_sdk.validation.golden_provider import GoldenProvider
from kamiwaza_sdk.validation.provider import ProviderContractError
from kamiwaza_sdk.validation.testkit import (
    RecordingFixtureStateWriter,
    exercise_provider_contract,
)

from .support import profile_payload

pytestmark = pytest.mark.contract


def _profile() -> ValidationProfile:
    payload = profile_payload()
    payload["validation"]["include"] = ["sdk.golden.echo/v1"]  # type: ignore[index]
    return ValidationProfile.model_validate(payload)


def _runtime() -> RuntimeContext:
    return RuntimeContext.model_validate(
        {
            "schema": "kamiwaza.runtime-context/v1",
            "run_id": "contract-run",
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


def test_golden_provider_passes_the_shared_contract_kit() -> None:
    result = exercise_provider_contract(GoldenProvider(), _profile(), _runtime())

    assert result.coverage.status == "passed"
    assert result.cleanup.status == "passed"
    assert result.plan.provider_revision == "sdk.golden@v1"


def test_golden_provider_snapshots_state_before_and_after_each_mutation() -> None:
    provider = GoldenProvider()
    writer = RecordingFixtureStateWriter()

    state = provider.prepare(provider.resolve(_profile()), _runtime(), writer)

    assert [len(snapshot.journal) for snapshot in writer.snapshots] == [0, 1]
    assert writer.snapshots[-1] == state


class NondeterministicGoldenProvider(GoldenProvider):
    calls = 0

    def describe(self):  # type: ignore[no-untyped-def]
        self.calls += 1
        descriptors = super().describe()
        if self.calls == 1:
            return descriptors
        return ()


def test_contract_kit_rejects_nondeterministic_describe() -> None:
    with pytest.raises(ProviderContractError, match="describe is not deterministic"):
        exercise_provider_contract(
            NondeterministicGoldenProvider(), _profile(), _runtime()
        )


class UndescribedCaseGoldenProvider(GoldenProvider):
    def resolve(self, profile):  # type: ignore[no-untyped-def]
        plan = super().resolve(profile)
        selected = plan.selected[0].model_copy(update={"case_ids": ("other",)})
        return plan.model_copy(update={"selected": (selected,)})


def test_contract_kit_rejects_cases_absent_from_descriptor_registry() -> None:
    with pytest.raises(ProviderContractError, match="undescribed case"):
        exercise_provider_contract(
            UndescribedCaseGoldenProvider(), _profile(), _runtime()
        )


def test_golden_provider_refuses_tampered_fixture_ownership() -> None:
    provider = GoldenProvider()
    plan = provider.resolve(_profile())
    runtime = _runtime()
    state = provider.prepare(plan, runtime, RecordingFixtureStateWriter()).model_copy(
        update={"owner_token_digest": "sha256:" + "0" * 64}
    )

    with pytest.raises(ProviderContractError, match="ownership digest mismatch"):
        provider.teardown(runtime, state)
