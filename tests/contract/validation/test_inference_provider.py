"""Strict product-semantic contracts for the local inference provider."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from kamiwaza_sdk.validation import RuntimeContext, ValidationProfile, model_digest
from kamiwaza_sdk.validation.inference_provider import (
    INFERENCE_CASE_IDS,
    INFERENCE_PROVIDER_REVISION,
    INFERENCE_SCENARIO_ID,
    InferenceLifecycleProvider,
)
from kamiwaza_sdk.validation.inference_state import (
    runtime_ownership_key,
    sign_state,
)
from kamiwaza_sdk.validation.inference_runtime import (
    CatalogConfig,
    CatalogFile,
    CatalogModel,
    DeploymentRequest,
    ReadyDeployment,
    RuntimeObservation,
)
from kamiwaza_sdk.validation.models import FixtureMutation, FixtureState
from kamiwaza_sdk.validation.provider import ProviderContractError
from kamiwaza_sdk.validation.testkit import (
    RecordingFixtureStateWriter,
    exercise_provider_contract,
)
from tests.contract.validation.support import profile_payload

pytestmark = pytest.mark.contract


def _profile(**target_updates: object) -> ValidationProfile:
    payload = profile_payload()
    target = payload["inference_targets"][0]  # type: ignore[index]
    target.update(target_updates)  # type: ignore[union-attr]
    return ValidationProfile.model_validate(payload)


def _runtime() -> RuntimeContext:
    api_key_ref = Path(__file__).with_name("test-api-key.txt").resolve().as_uri()
    ownership_key_path = Path(__file__).with_name("test-ownership-key.txt").resolve()
    ownership_key_path.chmod(0o600)
    ownership_key_ref = ownership_key_path.as_uri()
    return RuntimeContext.model_validate(
        {
            "schema": "kamiwaza.runtime-context/v1",
            "run_id": "run-inference-1",
            "ownership_key_ref": ownership_key_ref,
            "clusters": [
                {
                    "id": "evo-x2-2",
                    "base_url": "https://evo-x2-2.test/api",
                    "api_key_ref": api_key_ref,
                    "kubeconfig_ref": "file:///run/secrets/evo-x2-2.kubeconfig",
                }
            ],
        }
    )


class FakeCluster:
    def __init__(self) -> None:
        self.model = CatalogModel(
            model_id="11111111-1111-1111-1111-111111111111",
            repository="Qwen/Qwen3-0.6B-GGUF",
            files=(
                CatalogFile(
                    file_id="22222222-2222-2222-2222-222222222222",
                    name="qwen-q8_0.gguf",
                    ready=True,
                ),
            ),
        )
        self.configs = (
            CatalogConfig(
                config_id="33333333-3333-3333-3333-333333333333",
                default=True,
            ),
        )
        self.ready = ReadyDeployment(engine="llamacpp", instance_count=1)
        self.observation = RuntimeObservation(
            image_digest="sha256:" + "a" * 64,
            effective_args=("--model", "/models/qwen-q8_0.gguf", "-ngl", "999"),
        )
        self.deployments: set[str] = set()
        self.requests: list[DeploymentRequest] = []
        self.messages: list[tuple[dict[str, str], ...]] = []
        self.quantization = "q8_0"
        self.fail_at: str | None = None
        self.close_error = False
        self.stop_calls: list[str] = []

    def discover(self, repository: str) -> CatalogModel:
        self._raise("catalog-discovery")
        assert repository == self.model.repository
        return self.model

    def ensure_download(self, repository: str, quantization: str) -> CatalogModel:
        self._raise("download-readiness")
        assert (repository, quantization) == (
            self.model.repository,
            self.quantization,
        )
        return self.model

    def list_configs(self, model_id: str) -> tuple[CatalogConfig, ...]:
        self._raise("exact-model-file-selection")
        assert model_id == self.model.model_id
        return self.configs

    def deploy(self, request: DeploymentRequest) -> str:
        self._raise("explicit-engine-deployment")
        self.requests.append(request)
        deployment_id = "44444444-4444-4444-4444-444444444444"
        self.deployments.add(deployment_id)
        return deployment_id

    def wait_ready(self, deployment_id: str) -> ReadyDeployment:
        self._raise("deployment-readiness")
        assert deployment_id in self.deployments
        return self.ready

    def observe_runtime(self, deployment_id: str, engine: str) -> RuntimeObservation:
        assert deployment_id in self.deployments
        assert engine == self.ready.engine
        return self.observation

    def chat(self, deployment_id: str, messages: tuple[dict[str, str], ...]) -> str:
        self._raise("openai-multi-turn-chat")
        assert deployment_id in self.deployments
        self.messages.append(messages)
        return "hello" if len(self.messages) == 1 else "goodbye"

    def stop(self, deployment_id: str) -> bool:
        self._raise("deployment-stop")
        self.stop_calls.append(deployment_id)
        self.deployments.discard(deployment_id)
        return True

    def is_active(self, deployment_id: str) -> bool:
        self._raise("residual-cleanup")
        return deployment_id in self.deployments

    def close(self) -> None:
        if self.close_error:
            raise RuntimeError("close secret-value")

    def _raise(self, phase: str) -> None:
        if self.fail_at == phase:
            raise RuntimeError(f"{phase} secret-value")


class FakeFactory:
    def __init__(self, cluster: FakeCluster) -> None:
        self.cluster = cluster
        self.cluster_ids: list[str] = []

    def __call__(self, runtime_cluster: object) -> FakeCluster:
        self.cluster_ids.append(str(getattr(runtime_cluster, "id")))
        return self.cluster


def _prepared(
    provider: InferenceLifecycleProvider,
):
    profile = _profile()
    plan = provider.resolve(profile)
    writer = RecordingFixtureStateWriter()
    state = provider.prepare(plan, _runtime(), writer)
    return plan, writer, state


def test_describe_and_resolve_publish_exact_required_lifecycle() -> None:
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(FakeCluster()))

    descriptor = provider.describe()[0]
    plan = provider.resolve(_profile())

    assert descriptor.scenario_id == INFERENCE_SCENARIO_ID
    assert descriptor.case_ids == INFERENCE_CASE_IDS
    assert descriptor.requires == ("cluster-api", "kube-api", "ownership-key")
    assert descriptor.fixture_modes == ("owned",)
    assert plan.provider_revision == INFERENCE_PROVIDER_REVISION
    assert plan.runtime_requirements == (
        "cluster-api",
        "kube-api",
        "ownership-key",
    )
    assert len(plan.selected) == 1
    selected = plan.selected[0]
    assert selected.required is True
    assert selected.case_ids == INFERENCE_CASE_IDS
    assert selected.redacted_parameters == {
        "accelerators": [{"architecture": "gfx1151", "count": 1, "vendor": "amd"}],
        "engine": "llamacpp",
        "expected_image": None,
        "model_format": "gguf",
        "quantization": "q8_0",
        "repository": "Qwen/Qwen3-0.6B-GGUF",
        "runtime_profile": "product-default",
    }


def test_inference_provider_module_is_an_executable_provider_command() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "kamiwaza_sdk.validation.inference_provider",
            "describe",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    catalog = json.loads(completed.stdout)
    assert catalog[0]["scenario_id"] == INFERENCE_SCENARIO_ID
    assert catalog[0]["case_ids"] == list(INFERENCE_CASE_IDS)


@pytest.mark.parametrize(
    "updates",
    [
        {"model_format": "safetensors"},
        {"runtime_profile": "raw-cli-args"},
    ],
)
def test_resolve_rejects_incompatible_required_target(
    updates: dict[str, object],
) -> None:
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(FakeCluster()))

    with pytest.raises(ProviderContractError, match="incompatible required target"):
        provider.resolve(_profile(**updates))


def test_resolve_rejects_external_fixture_mode_until_adoption_exists() -> None:
    profile = _profile()
    profile = profile.model_copy(
        update={
            "validation": profile.validation.model_copy(
                update={"fixture_mode": "external"}
            )
        }
    )
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(FakeCluster()))

    with pytest.raises(ProviderContractError, match="fixture mode"):
        provider.resolve(profile)


def test_default_activation_rejects_required_unsupported_engine() -> None:
    profile = _profile(engine="sglang")
    profile = profile.model_copy(
        update={"validation": profile.validation.model_copy(update={"include": ()})}
    )
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(FakeCluster()))

    with pytest.raises(ProviderContractError, match="incompatible required target"):
        provider.resolve(profile)


def test_resolve_rejects_vllm_quantization_without_artifact_semantics() -> None:
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(FakeCluster()))

    with pytest.raises(ProviderContractError, match="incompatible required target"):
        provider.resolve(
            _profile(
                engine="vllm",
                model_format="safetensors",
                quantization="q4_k_m",
            )
        )


def test_resolve_rejects_vllm_architecture_outside_v1_support_policy() -> None:
    payload = profile_payload()
    payload["clusters"][0]["hardware"] = {  # type: ignore[index]
        "accelerators": [{"vendor": "nvidia", "architecture": "turing", "count": 1}]
    }
    payload["inference_targets"][0].update(  # type: ignore[index,union-attr]
        {
            "engine": "vllm",
            "model_format": "safetensors",
            "quantization": "none",
        }
    )
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(FakeCluster()))

    with pytest.raises(ProviderContractError, match="incompatible required target"):
        provider.resolve(ValidationProfile.model_validate(payload))


def test_happy_lifecycle_records_exact_selection_runtime_and_cleanup() -> None:
    cluster = FakeCluster()
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(cluster))
    plan, writer, state = _prepared(provider)

    evidence = provider.run(plan, _runtime(), state)
    cleanup = provider.teardown(_runtime(), state)

    assert len(writer.snapshots) >= 2
    assert writer.snapshots[0].journal == ()
    assert all("ownership_mac" in snapshot.opaque for snapshot in writer.snapshots)
    assert len(state.journal) == 1
    assert state.journal[0].action == "created"
    assert state.journal[0].resource_id == "44444444-4444-4444-4444-444444444444"
    assert [(item.case_id, item.status) for item in evidence.results] == [
        (case_id, "passed") for case_id in INFERENCE_CASE_IDS
    ]
    assert evidence.state_digest == model_digest(state)
    runtime = evidence.resolved_runtime["evo-x2-2-llamacpp-chat"]
    assert runtime["actual_engine"] == "llamacpp"  # type: ignore[index]
    assert runtime["actual_image_digest"] == "sha256:" + "a" * 64  # type: ignore[index]
    assert runtime["effective_runtime_args"][-2:] == ["-ngl", "999"]  # type: ignore[index]
    assert runtime["model_file_id"] == "22222222-2222-2222-2222-222222222222"  # type: ignore[index]
    assert cluster.requests == [
        DeploymentRequest(
            model_id="11111111-1111-1111-1111-111111111111",
            config_id="33333333-3333-3333-3333-333333333333",
            model_file_id="22222222-2222-2222-2222-222222222222",
            engine="llamacpp",
            runtime_profile="product-default",
        )
    ]
    assert len(cluster.messages) == 2
    assert cluster.messages[1][1] == {"role": "assistant", "content": "hello"}
    assert cleanup.status == "passed"
    assert cleanup.results[0].status == "absent"


def test_provider_passes_reusable_exact_lifecycle_contract_kit() -> None:
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(FakeCluster()))

    result = exercise_provider_contract(provider, _profile(), _runtime())

    assert result.coverage.status == "passed"
    assert result.cleanup.status == "passed"


def test_vllm_selects_exact_shard_set_without_ambiguous_file_override() -> None:
    payload = profile_payload()
    payload["clusters"][0]["hardware"] = {  # type: ignore[index]
        "accelerators": [{"vendor": "nvidia", "architecture": "gb10", "count": 1}]
    }
    payload["inference_targets"][0].update(  # type: ignore[index,union-attr]
        {
            "repository": "Qwen/Qwen3-0.6B",
            "engine": "vllm",
            "model_format": "safetensors",
            "quantization": "none",
        }
    )
    cluster = FakeCluster()
    cluster.model = CatalogModel(
        model_id=cluster.model.model_id,
        repository="Qwen/Qwen3-0.6B",
        files=(
            CatalogFile("file-b", "model-00002-of-00002.safetensors", True),
            CatalogFile("file-a", "model-00001-of-00002.safetensors", True),
        ),
    )
    cluster.quantization = "none"
    cluster.ready = ReadyDeployment(engine="vllm", instance_count=1)
    cluster.observation = RuntimeObservation(
        image_digest="sha256:" + "c" * 64,
        effective_args=("vllm", "serve", "/models/qwen"),
    )
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(cluster))
    plan = provider.resolve(ValidationProfile.model_validate(payload))
    writer = RecordingFixtureStateWriter()

    state = provider.prepare(plan, _runtime(), writer)
    evidence = provider.run(plan, _runtime(), state)

    assert cluster.requests[0].engine == "vllm"
    assert cluster.requests[0].model_file_id is None
    runtime = evidence.resolved_runtime["evo-x2-2-llamacpp-chat"]
    assert runtime["model_files"] == [  # type: ignore[index]
        {"id": "file-a", "name": "model-00001-of-00002.safetensors"},
        {"id": "file-b", "name": "model-00002-of-00002.safetensors"},
    ]
    assert all(item.status == "passed" for item in evidence.results)


def test_prepare_failure_becomes_exact_failed_evidence_without_skip() -> None:
    cluster = FakeCluster()
    cluster.fail_at = "download-readiness"
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(cluster))
    plan, _writer, state = _prepared(provider)

    evidence = provider.run(plan, _runtime(), state)
    cleanup = provider.teardown(_runtime(), state)

    assert len(evidence.results) == len(INFERENCE_CASE_IDS)
    assert all(item.status == "failed" for item in evidence.results[1:])
    assert all(item.status != "skipped" for item in evidence.results)
    assert "secret-value" not in str(evidence)
    assert state.journal == ()
    assert cleanup.status == "passed"
    assert cleanup.results == ()


def test_readiness_failure_still_stops_and_reconciles_created_deployment() -> None:
    cluster = FakeCluster()
    cluster.fail_at = "deployment-readiness"
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(cluster))
    plan, _writer, state = _prepared(provider)

    evidence = provider.run(plan, _runtime(), state)
    cleanup = provider.teardown(_runtime(), state)

    by_case = {item.case_id: item for item in evidence.results}
    assert by_case["deployment-readiness"].status == "failed"
    assert by_case["openai-multi-turn-chat"].status == "failed"
    assert by_case["deployment-stop"].status == "passed"
    assert by_case["residual-cleanup"].status == "passed"
    assert cleanup.results[0].status == "absent"


def test_run_rejects_opaque_deployment_id_not_bound_to_created_journal() -> None:
    cluster = FakeCluster()
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(cluster))
    plan, _writer, state = _prepared(provider)
    targets = dict(state.opaque["targets"])  # type: ignore[arg-type]
    target = dict(targets["evo-x2-2-llamacpp-chat"])  # type: ignore[arg-type]
    target["deployment_id"] = "55555555-5555-5555-5555-555555555555"
    targets["evo-x2-2-llamacpp-chat"] = target
    tampered = state.model_copy(update={"opaque": {"targets": targets}})

    with pytest.raises(ProviderContractError, match="ownership MAC"):
        provider.run(plan, _runtime(), tampered)

    assert cluster.messages == []
    assert cluster.stop_calls == []


def test_run_rejects_adopted_deployment_without_chat_or_stop() -> None:
    cluster = FakeCluster()
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(cluster))
    plan, _writer, state = _prepared(provider)
    adopted = state.journal[0].model_copy(update={"action": "adopted"})
    tampered = state.model_copy(update={"journal": (adopted,)})
    tampered = sign_state(tampered, runtime_ownership_key(_runtime()))

    with pytest.raises(ProviderContractError, match="owned deployment"):
        provider.run(plan, _runtime(), tampered)

    assert cluster.messages == []
    assert cluster.stop_calls == []


def test_run_rejects_tampered_owner_digest_without_product_calls() -> None:
    cluster = FakeCluster()
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(cluster))
    plan, _writer, state = _prepared(provider)
    tampered = state.model_copy(update={"owner_token_digest": "sha256:" + "0" * 64})

    with pytest.raises(ProviderContractError, match="ownership digest"):
        provider.run(plan, _runtime(), tampered)

    assert cluster.messages == []
    assert cluster.stop_calls == []


def test_teardown_retains_adopted_deployment_without_mutation() -> None:
    cluster = FakeCluster()
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(cluster))
    _plan, _writer, state = _prepared(provider)
    adopted = state.journal[0].model_copy(update={"action": "adopted"})
    tampered = state.model_copy(update={"journal": (adopted,)})
    tampered = sign_state(tampered, runtime_ownership_key(_runtime()))

    cleanup = provider.teardown(_runtime(), tampered)

    assert cleanup.status == "passed"
    assert cleanup.results[0].status == "retained_foreign"
    assert cluster.stop_calls == []


def test_teardown_rejects_removed_deployment_state_without_mutation() -> None:
    cluster = FakeCluster()
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(cluster))
    _plan, _writer, state = _prepared(provider)
    removed = FixtureMutation(
        sequence=2,
        target_id=state.journal[0].target_id,
        resource_type="model-deployment",
        resource_id=state.journal[0].resource_id,
        action="removed",
    )
    transitioned = state.model_copy(update={"journal": (*state.journal, removed)})
    transitioned = sign_state(transitioned, runtime_ownership_key(_runtime()))

    cleanup = provider.teardown(_runtime(), transitioned)

    assert cleanup.status == "failed"
    assert cleanup.results[0].status == "failed"
    assert cluster.stop_calls == []


def test_teardown_rejects_multiple_deployments_for_one_target_without_mutation() -> (
    None
):
    cluster = FakeCluster()
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(cluster))
    _plan, _writer, state = _prepared(provider)
    foreign = FixtureMutation(
        sequence=2,
        target_id=state.journal[0].target_id,
        resource_type="model-deployment",
        resource_id="55555555-5555-5555-5555-555555555555",
        action="created",
    )
    tampered = state.model_copy(update={"journal": (*state.journal, foreign)})
    tampered = sign_state(tampered, runtime_ownership_key(_runtime()))

    with pytest.raises(ProviderContractError, match="multiple model deployments"):
        provider.teardown(_runtime(), tampered)

    assert cluster.stop_calls == []


class _FailDeploymentJournalWriter:
    def __init__(self) -> None:
        self.snapshots: list[FixtureState] = []

    def write(self, state: FixtureState) -> None:
        if state.journal:
            raise OSError("persistence secret-value")
        self.snapshots.append(state)


def test_deployment_is_compensated_when_ownership_journal_cannot_persist() -> None:
    cluster = FakeCluster()
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(cluster))
    plan = provider.resolve(_profile())
    writer = _FailDeploymentJournalWriter()

    with pytest.raises(OSError, match="persistence secret-value"):
        provider.prepare(plan, _runtime(), writer)

    assert cluster.deployments == set()
    assert cluster.stop_calls == ["44444444-4444-4444-4444-444444444444"]


def test_client_close_failure_does_not_erase_lifecycle_evidence() -> None:
    cluster = FakeCluster()
    cluster.close_error = True
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(cluster))

    plan, _writer, state = _prepared(provider)
    evidence = provider.run(plan, _runtime(), state)
    cleanup = provider.teardown(_runtime(), state)

    assert all(item.status == "passed" for item in evidence.results)
    assert cleanup.status == "passed"


def test_expected_image_mismatch_fails_readiness_but_preserves_observation() -> None:
    cluster = FakeCluster()
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(cluster))
    profile = _profile(expected_image="sha256:" + "b" * 64)
    plan = provider.resolve(profile)
    assert plan.install_requirements == {
        "inference_images": {"evo-x2-2-llamacpp-chat": "sha256:" + "b" * 64}
    }
    writer = RecordingFixtureStateWriter()
    state = provider.prepare(plan, _runtime(), writer)

    evidence = provider.run(plan, _runtime(), state)

    by_case = {item.case_id: item for item in evidence.results}
    assert by_case["deployment-readiness"].status == "failed"
    assert by_case["openai-multi-turn-chat"].status == "failed"
    runtime = evidence.resolved_runtime["evo-x2-2-llamacpp-chat"]
    assert runtime["actual_image_digest"] == cluster.observation.image_digest  # type: ignore[index]


def test_teardown_reports_failure_when_owned_deployment_remains_active() -> None:
    cluster = FakeCluster()
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(cluster))
    _plan, _writer, state = _prepared(provider)
    cluster.fail_at = "deployment-stop"

    cleanup = provider.teardown(_runtime(), state)

    assert cleanup.status == "failed"
    assert cleanup.results[0].status == "failed"
    assert "secret-value" not in str(cleanup)


def test_vllm_requires_supported_accelerator() -> None:
    payload = profile_payload()
    cluster = payload["clusters"][0]  # type: ignore[index]
    cluster["hardware"] = {"accelerators": []}  # type: ignore[index]
    target = payload["inference_targets"][0]  # type: ignore[index]
    target.update(  # type: ignore[union-attr]
        {
            "engine": "vllm",
            "model_format": "safetensors",
            "quantization": "none",
        }
    )
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(FakeCluster()))

    with pytest.raises(ProviderContractError, match="incompatible required target"):
        provider.resolve(ValidationProfile.model_validate(payload))


def test_optional_incompatible_target_is_filtered_before_execution() -> None:
    profile = _profile(required=False, model_format="safetensors")
    profile = profile.model_copy(
        update={"validation": profile.validation.model_copy(update={"include": ()})}
    )
    provider = InferenceLifecycleProvider(cluster_factory=FakeFactory(FakeCluster()))

    assert provider.resolve(profile).selected == ()
