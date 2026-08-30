"""Contract tests for the federated model-mesh scenario inventory."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kamiwaza_sdk.validation import (
    RuntimeContext,
    ValidationProfile,
    mesh_edge_target_id,
)
from kamiwaza_sdk.validation.applicability import applicable_targets
from kamiwaza_sdk.validation.model_mesh_provider import ModelMeshLifecycleProvider
from kamiwaza_sdk.validation.federation_common import initial_tuples
from kamiwaza_sdk.validation.federation_spec import scenario_descriptor
from kamiwaza_sdk.validation.model_mesh_spec import (
    MODEL_MESH_CASE_IDS,
    MODEL_MESH_SCENARIO_ID,
    resolve_candidates,
    scenario_descriptor as model_mesh_descriptor,
)
from kamiwaza_sdk.validation.provider import ProviderContractError
from kamiwaza_sdk.validation.testkit import RecordingFixtureStateWriter
from tests.contract.validation.support import profile_payload

pytestmark = pytest.mark.contract


def _profile(*, include: bool = True, compatible: bool = True) -> ValidationProfile:
    payload = profile_payload()
    payload["validation"] = {
        "level": "standard",
        "fixture_mode": "owned",
        "include": [MODEL_MESH_SCENARIO_ID] if include else [],
        "exclude": [],
    }
    payload["clusters"] = [
        {
            "id": "edge-a",
            "roles": ["controller"],
            "node_count": 1,
            "hardware": {"accelerators": []},
            "features": {"rebac": True},
        },
        {
            "id": "edge-b",
            "roles": ["controller", "inference"],
            "node_count": 1,
            "hardware": {
                "accelerators": [
                    {"vendor": "amd", "architecture": "gfx1151", "count": 1}
                ]
            },
            "features": {"rebac": True},
        },
    ]
    payload["mesh"] = {
        "edges": [
            {"initiator": "edge-a", "receiver": "edge-b", "identity_mode": "shared_idp"}
        ]
    }
    payload["inference_targets"] = [
        {
            "id": "edge-b-llamacpp-chat",
            "cluster_id": "edge-b",
            "required": True,
            "repository": "Qwen/Qwen3-0.6B-GGUF",
            "engine": "llamacpp",
            "model_format": "gguf" if compatible else "safetensors",
            "quantization": "q8_0",
            "runtime_profile": "product-default",
            "expected_image": None,
        }
    ]
    return ValidationProfile.model_validate(payload)


def test_model_mesh_descriptor_is_a_distinct_exact_inventory() -> None:
    descriptor = model_mesh_descriptor()

    assert descriptor.scenario_id == MODEL_MESH_SCENARIO_ID
    assert descriptor.provider_id == "sdk.federation.model-mesh"
    assert descriptor.target_scope == "mesh_edge"
    assert descriptor.minimum_level == "standard"
    assert descriptor.case_ids == MODEL_MESH_CASE_IDS
    assert descriptor.case_ids != scenario_descriptor().case_ids


def test_model_mesh_resolution_binds_receiver_model_target_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAMIWAZA_SHARED_IDP_PUBLIC_URL", "https://idp.test")
    monkeypatch.setenv("KAMIWAZA_VALIDATION_RUN_ID", "run-model-mesh-1")
    profile = _profile()
    edge = profile.mesh.edges[0]
    candidate = next(
        item
        for item in applicable_targets(profile, model_mesh_descriptor())
        if item.target_id == mesh_edge_target_id(edge)
    )

    first = resolve_candidates(profile, (candidate,), explicit=True)
    second = resolve_candidates(profile, (candidate,), explicit=True)

    assert first == second
    assert first[0].target_id == mesh_edge_target_id(edge)
    assert first[0].cluster_ids == ("edge-a", "edge-b")
    assert first[0].case_ids == MODEL_MESH_CASE_IDS
    assert first[0].redacted_parameters["model_target_id"] == "edge-b-llamacpp-chat"
    assert first[0].redacted_parameters["model_engine"] == "llamacpp"


def test_model_mesh_resolution_rejects_an_edge_without_compatible_model_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAMIWAZA_SHARED_IDP_PUBLIC_URL", "https://idp.test")
    profile = _profile(compatible=False)
    candidates = applicable_targets(profile, model_mesh_descriptor())

    with pytest.raises(ProviderContractError, match="compatible model target"):
        resolve_candidates(profile, candidates, explicit=True)


def test_model_mesh_provider_publishes_the_exact_model_mesh_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAMIWAZA_SHARED_IDP_PUBLIC_URL", "https://idp.test")
    monkeypatch.setenv("KAMIWAZA_VALIDATION_RUN_ID", "run-model-mesh-1")

    plan = ModelMeshLifecycleProvider().resolve(_profile())

    assert plan.provider_revision == "sdk.federation.model-mesh@v1"
    assert len(plan.selected) == 1
    assert plan.selected[0].scenario_id == MODEL_MESH_SCENARIO_ID
    # Install requirements are consumed by Kajiya's strict allowlist.  The
    # selected model target is already carried by the resolved scenario and
    # must not be emitted as an unconsumed top-level requirement.
    assert plan.install_requirements == {
        "scheduler": {
            "trustedSharedIssuers": [
                "https://idp.test/realms/kz-validation-f0bad37f5cec6b3d"
            ]
        }
    }


def test_model_mesh_grant_tuple_does_not_inherit_retrieval_or_job_authority() -> None:
    assert initial_tuples(model_id="model-123", job_executor=False) == [
        {
            "subject": "user:{{user_id}}",
            "relation": "viewer",
            "object": "model:model-123",
        }
    ]


def _jwt(subject: str) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = (
        base64.urlsafe_b64encode(json.dumps({"sub": subject}).encode())
        .decode()
        .rstrip("=")
    )
    return f"{header}.{payload}.signature"


def _runtime(tmp_path: Path) -> RuntimeContext:
    ownership = tmp_path / "ownership.key"
    ownership.write_bytes(b"o" * 48)
    ownership.chmod(0o600)
    password = tmp_path / "persona.password"
    password.write_text("persona-secret\n", encoding="utf-8")
    return RuntimeContext.model_validate(
        {
            "schema": "kamiwaza.runtime-context/v1",
            "run_id": "run-model-mesh-1",
            "ownership_key_ref": ownership.as_uri(),
            "secret_refs": {
                "shared-idp-admin-password": "file:///run/secrets/admin.password",
                "shared-idp-persona-password": password.as_uri(),
            },
            "clusters": [
                {
                    "id": "edge-a",
                    "base_url": "https://edge-a.test/api",
                    "api_key_ref": "file:///run/secrets/edge-a.api-key",
                    "kubeconfig_ref": "file:///run/secrets/edge-a.kubeconfig",
                },
                {
                    "id": "edge-b",
                    "base_url": "https://edge-b.test/api",
                    "api_key_ref": "file:///run/secrets/edge-b.api-key",
                    "kubeconfig_ref": "file:///run/secrets/edge-b.kubeconfig",
                },
            ],
        }
    )


class _Users:
    def __init__(self) -> None:
        self.added: list[tuple[str, list[dict[str, str]]]] = []

    def add(self, external_id: str, *, initial_tuples: list[dict[str, str]]) -> None:
        self.added.append((external_id, initial_tuples))


class _Federations:
    def __init__(self, cluster_id: str, remote_id: str) -> None:
        self.cluster_id = cluster_id
        self.remote_id = remote_id
        self.users = _Users()

    def pair(self, *, role: str, **kwargs: Any) -> dict[str, str]:
        del kwargs
        return {"id": f"{role}-{self.cluster_id}-fed"}

    def get(self, federation_id: str) -> dict[str, str]:
        del federation_id
        return {"remote_cluster_id": self.remote_id}

    def by_id(self, federation_id: str, *, remote_name: str | None = None) -> Any:
        del federation_id, remote_name
        return SimpleNamespace(users=self.users)

    def __getitem__(self, name: str) -> Any:
        raise AssertionError(f"name-based federation lookup is not allowed: {name}")


class _Client:
    def __init__(self, cluster_id: str, remote_id: str) -> None:
        self.federations = _Federations(cluster_id, remote_id)
        self.cluster = SimpleNamespace()
        self.serving = SimpleNamespace(stop_deployment=lambda **kwargs: True)

    def close(self) -> None:
        return None


class _ClusterWrapper:
    def __init__(self, client: _Client) -> None:
        self.client = client

    def close(self) -> None:
        self.client.close()


class _ClusterFactory:
    def __init__(self) -> None:
        self.wrappers = {
            "edge-a": _ClusterWrapper(_Client("edge-a", "edge-b")),
            "edge-b": _ClusterWrapper(_Client("edge-b", "edge-a")),
        }

    def __call__(self, runtime_cluster: Any) -> _ClusterWrapper:
        return self.wrappers[str(runtime_cluster.id)]


class _Admin:
    def create_owned_realm(self, realm: str, owner_nonce: str) -> dict[str, Any]:
        del owner_nonce
        return {"realm": realm, "created": True}

    def set_unmanaged_attributes(self, realm: str, *, policy: str = "ENABLED") -> None:
        del realm, policy

    def ensure_ropc_client(self, realm: str, client_id: str) -> dict[str, str]:
        del realm, client_id
        return {"id": "client-uuid"}

    def ensure_attribute_mapper(
        self, realm: str, client_uuid: str, *, attribute: str
    ) -> None:
        del realm, client_uuid, attribute

    def ensure_user(
        self, realm: str, username: str, *, password: str, attributes: dict[str, Any]
    ) -> dict[str, str]:
        del realm, password, attributes
        return {"id": f"user-{username}"}

    def ropc_token(
        self, realm: str, client_id: str, username: str, password: str
    ) -> str:
        del realm, client_id, password
        return _jwt(username)


class _AdminFactory:
    def __init__(self, admin: _Admin) -> None:
        self.admin = admin

    def __call__(self, runtime: RuntimeContext, cluster: Any) -> _Admin:
        del runtime, cluster
        return self.admin


class _Inference:
    def __init__(self) -> None:
        self.stopped: list[str] = []

    def ensure_download(self, repository: str, quantization: str) -> Any:
        from kamiwaza_sdk.validation.inference_runtime import CatalogFile, CatalogModel

        return CatalogModel(
            model_id="model-123",
            repository=repository,
            files=(CatalogFile("file-123", "Qwen3-q8_0.gguf", True),),
        )

    def list_configs(self, model_id: str) -> tuple[Any, ...]:
        from kamiwaza_sdk.validation.inference_runtime import CatalogConfig

        return (CatalogConfig("config-123", True),)

    def deploy(self, request: Any) -> str:
        assert request.model_id == "model-123"
        return "deployment-123"

    def wait_ready(self, deployment_id: str) -> Any:
        from kamiwaza_sdk.validation.inference_runtime import ReadyDeployment

        assert deployment_id == "deployment-123"
        return ReadyDeployment("llamacpp", 1)

    def observe_runtime(self, deployment_id: str, engine: str) -> Any:
        from kamiwaza_sdk.validation.inference_runtime import RuntimeObservation

        assert (deployment_id, engine) == ("deployment-123", "llamacpp")
        return RuntimeObservation("sha256:" + "a" * 64, ("--n-gpu-layers", "999"))

    def served_model_id(self, deployment_id: str) -> str:
        assert deployment_id == "deployment-123"
        return "served-qwen"

    def stop(self, deployment_id: str) -> bool:
        self.stopped.append(deployment_id)
        return True

    def close(self) -> None:
        return None


class _InferenceFactory:
    def __init__(self, inference: _Inference) -> None:
        self.inference = inference

    def __call__(self, runtime_cluster: Any) -> _Inference:
        del runtime_cluster
        return self.inference


def test_model_mesh_prepare_owns_only_pairing_model_grant_and_no_gate_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KAMIWAZA_SHARED_IDP_PUBLIC_URL", "https://idp.test")
    monkeypatch.setenv("KAMIWAZA_VALIDATION_RUN_ID", "run-model-mesh-1")
    profile = _profile()
    runtime = _runtime(tmp_path)
    cluster_factory = _ClusterFactory()
    admin = _Admin()
    inference = _Inference()
    provider = ModelMeshLifecycleProvider(
        cluster_factory=cluster_factory,
        admin_factory=_AdminFactory(admin),
        inference_factory=_InferenceFactory(inference),
    )
    plan = provider.resolve(profile)
    state = provider.prepare(plan, runtime, RecordingFixtureStateWriter())

    resource_types = [item.resource_type for item in state.journal]
    assert "model-deployment" in resource_types
    assert "dataset" not in resource_types
    assert "gate-package" not in resource_types
    assert "execution-gate" not in resource_types
    receiver_users = cluster_factory.wrappers["edge-b"].client.federations.users.added
    assert receiver_users
    user_tuples = [
        tuples for external_id, tuples in receiver_users if "fed-clr-u" in external_id
    ]
    assert user_tuples == [
        [
            {
                "subject": "user:{{user_id}}",
                "relation": "viewer",
                "object": "model:model-123",
            },
            {
                "subject": "user:{{user_id}}",
                "relation": "invoker",
                "object": "model:model-123",
            },
        ]
    ]
    edge = state.opaque["edges"][plan.selected[0].target_id]
    assert edge["resources"]["model_repository"] == "Qwen/Qwen3-0.6B-GGUF"
