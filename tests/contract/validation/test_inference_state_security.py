"""Security and platform-boundary contracts for inference fixture state."""

from __future__ import annotations

from pathlib import Path

import pytest

from kamiwaza_sdk.validation import ValidationProfile
from kamiwaza_sdk.validation.inference_provider import InferenceLifecycleProvider
from kamiwaza_sdk.validation.provider import ProviderContractError
from kamiwaza_sdk.validation.testkit import RecordingFixtureStateWriter
from tests.contract.validation import test_inference_provider as provider_contract
from tests.contract.validation.support import profile_payload

pytestmark = pytest.mark.contract


def test_resolve_rejects_apple_m5_until_host_runtime_observation_exists() -> None:
    payload = profile_payload()
    payload["clusters"][0]["hardware"] = {  # type: ignore[index]
        "accelerators": [{"vendor": "apple", "architecture": "m5", "count": 1}]
    }
    provider = InferenceLifecycleProvider(
        cluster_factory=provider_contract.FakeFactory(provider_contract.FakeCluster())
    )

    with pytest.raises(ProviderContractError, match="incompatible required target"):
        provider.resolve(ValidationProfile.model_validate(payload))


def test_run_and_teardown_reject_coordinated_resource_id_tampering() -> None:
    cluster = provider_contract.FakeCluster()
    factory = provider_contract.FakeFactory(cluster)
    provider = InferenceLifecycleProvider(cluster_factory=factory)
    plan, _writer, state = provider_contract._prepared(provider)
    foreign_id = "55555555-5555-5555-5555-555555555555"
    foreign = state.journal[0].model_copy(update={"resource_id": foreign_id})
    targets = dict(state.opaque["targets"])  # type: ignore[arg-type]
    target = dict(targets["evo-x2-2-llamacpp-chat"])  # type: ignore[arg-type]
    target["deployment_id"] = foreign_id
    targets["evo-x2-2-llamacpp-chat"] = target
    opaque = dict(state.opaque)
    opaque["targets"] = targets
    tampered = state.model_copy(update={"journal": (foreign,), "opaque": opaque})
    factory_calls = len(factory.cluster_ids)

    with pytest.raises(ProviderContractError, match="ownership MAC mismatch"):
        provider.run(plan, provider_contract._runtime(), tampered)
    with pytest.raises(ProviderContractError, match="ownership MAC mismatch"):
        provider.teardown(provider_contract._runtime(), tampered)

    assert len(factory.cluster_ids) == factory_calls
    assert cluster.messages == []
    assert cluster.stop_calls == []


def test_run_rejects_state_after_runtime_credential_changes(tmp_path: Path) -> None:
    credential = tmp_path / "api-key"
    credential.write_text("first-test-credential", encoding="utf-8")
    runtime = provider_contract._runtime()
    runtime_cluster = runtime.clusters[0].model_copy(
        update={"api_key_ref": credential.resolve().as_uri()}
    )
    runtime = runtime.model_copy(update={"clusters": (runtime_cluster,)})
    cluster = provider_contract.FakeCluster()
    factory = provider_contract.FakeFactory(cluster)
    provider = InferenceLifecycleProvider(cluster_factory=factory)
    plan = provider.resolve(provider_contract._profile())
    state = provider.prepare(plan, runtime, RecordingFixtureStateWriter())
    factory_calls = len(factory.cluster_ids)
    credential.write_text("second-test-credential", encoding="utf-8")

    with pytest.raises(ProviderContractError, match="ownership MAC mismatch"):
        provider.run(plan, runtime, state)

    assert len(factory.cluster_ids) == factory_calls
    assert cluster.messages == []
    assert cluster.stop_calls == []


def test_prepare_rejects_unavailable_runtime_credential_before_product_calls() -> None:
    runtime = provider_contract._runtime()
    runtime_cluster = runtime.clusters[0].model_copy(
        update={"api_key_ref": "file:///definitely/missing/inference.pat"}
    )
    runtime = runtime.model_copy(update={"clusters": (runtime_cluster,)})
    factory = provider_contract.FakeFactory(provider_contract.FakeCluster())
    provider = InferenceLifecycleProvider(cluster_factory=factory)
    plan = provider.resolve(provider_contract._profile())

    with pytest.raises(ProviderContractError, match="API key file is unavailable"):
        provider.prepare(plan, runtime, RecordingFixtureStateWriter())

    assert factory.cluster_ids == []
