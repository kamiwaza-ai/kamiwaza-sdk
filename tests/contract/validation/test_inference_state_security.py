"""Security and platform-boundary contracts for inference fixture state."""

from __future__ import annotations

import os
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


def test_run_allows_api_credential_rotation_with_stable_ownership_key(
    tmp_path: Path,
) -> None:
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

    evidence = provider.run(plan, runtime, state)

    assert len(factory.cluster_ids) == factory_calls + 1
    assert all(item.status == "passed" for item in evidence.results)
    assert len(cluster.messages) == 2
    assert cluster.stop_calls == ["44444444-4444-4444-4444-444444444444"]


def test_run_rejects_rotated_ownership_key_before_product_calls(
    tmp_path: Path,
) -> None:
    ownership_key = tmp_path / "ownership-key"
    ownership_key.write_text("first-test-ownership-key-material-01", encoding="utf-8")
    ownership_key.chmod(0o600)
    runtime = provider_contract._runtime().model_copy(
        update={"ownership_key_ref": ownership_key.resolve().as_uri()}
    )
    cluster = provider_contract.FakeCluster()
    factory = provider_contract.FakeFactory(cluster)
    provider = InferenceLifecycleProvider(cluster_factory=factory)
    plan = provider.resolve(provider_contract._profile())
    state = provider.prepare(plan, runtime, RecordingFixtureStateWriter())
    factory_calls = len(factory.cluster_ids)
    ownership_key.write_text("second-test-ownership-key-material-2", encoding="utf-8")

    with pytest.raises(ProviderContractError, match="ownership MAC mismatch"):
        provider.run(plan, runtime, state)

    assert len(factory.cluster_ids) == factory_calls
    assert cluster.messages == []
    assert cluster.stop_calls == []


def test_prepare_rejects_unavailable_ownership_key_before_product_calls() -> None:
    runtime = provider_contract._runtime()
    runtime = runtime.model_copy(
        update={
            "ownership_key_ref": "file:///definitely/missing/inference-ownership.key"
        }
    )
    factory = provider_contract.FakeFactory(provider_contract.FakeCluster())
    provider = InferenceLifecycleProvider(cluster_factory=factory)
    plan = provider.resolve(provider_contract._profile())

    with pytest.raises(ProviderContractError, match="ownership key file is unavailable"):
        provider.prepare(plan, runtime, RecordingFixtureStateWriter())

    assert factory.cluster_ids == []


@pytest.mark.parametrize(
    "case",
    [
        ("file", b"too-short", 0o600, "at least 32 bytes"),
        ("file", b"x" * 4097, 0o600, "at most 4096 bytes"),
        ("directory", b"", 0o700, "must be a regular file"),
        pytest.param(
            ("file", b"x" * 32, 0o644, "must not allow group or other access"),
            marks=pytest.mark.skipif(
                os.name != "posix", reason="POSIX permission contract"
            ),
        ),
    ],
)
def test_prepare_rejects_unsafe_ownership_key_before_product_calls(
    tmp_path: Path,
    case: tuple[str, bytes, int, str],
) -> None:
    kind, contents, mode, expected_error = case
    ownership_key = tmp_path / "ownership-key"
    if kind == "directory":
        ownership_key.mkdir(mode=mode)
    else:
        ownership_key.write_bytes(contents)
        ownership_key.chmod(mode)
    runtime = provider_contract._runtime().model_copy(
        update={"ownership_key_ref": ownership_key.resolve().as_uri()}
    )
    factory = provider_contract.FakeFactory(provider_contract.FakeCluster())
    provider = InferenceLifecycleProvider(cluster_factory=factory)
    plan = provider.resolve(provider_contract._profile())

    with pytest.raises(ProviderContractError, match=expected_error):
        provider.prepare(plan, runtime, RecordingFixtureStateWriter())

    assert factory.cluster_ids == []
