"""Contract tests for the target-aware validation protocol models."""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import ValidationError

from kamiwaza_sdk.validation import (
    CleanupEvidence,
    CleanupResult,
    CoverageIssue,
    CoverageSummary,
    ResolvedScenario,
    RuntimeContext,
    ScenarioCatalog,
    ScenarioDescriptor,
    ValidationProfile,
    mesh_edge_target_id,
)
from kamiwaza_sdk.validation.golden_provider import GoldenProvider
from kamiwaza_sdk.validation.provider import (
    ProviderContractError,
    validate_plan_identity,
    validate_plan_runtime_identity,
)
from kamiwaza_sdk.validation.registry import model_digest

from .support import profile_payload

pytestmark = pytest.mark.contract


def _mesh_edge(*, initiator: str = "evo-x2-2", receiver: str) -> dict[str, str]:
    return {
        "initiator": initiator,
        "receiver": receiver,
        "identity_mode": "shared_idp",
    }


def test_validation_profile_accepts_only_versioned_facts_and_intent() -> None:
    profile = ValidationProfile.model_validate(profile_payload())

    assert profile.schema_id == "kamiwaza.validation-profile/v1"
    assert profile.model_dump(mode="json", by_alias=True)["schema"] == (
        "kamiwaza.validation-profile/v1"
    )
    assert profile.clusters[0].hardware.accelerators[0].architecture == "gfx1151"
    assert profile.inference_targets[0].engine == "llamacpp"


def test_validation_profile_requires_immutable_expected_image_reference() -> None:
    payload = profile_payload()
    payload["inference_targets"][0]["expected_image"] = (  # type: ignore[index]
        "ghcr.io/kamiwaza/llamacpp:latest"
    )

    with pytest.raises(ValidationError, match="expected_image"):
        ValidationProfile.model_validate(payload)

    payload["inference_targets"][0]["expected_image"] = (  # type: ignore[index]
        "ghcr.io/kamiwaza/llamacpp@sha256:" + "a" * 64
    )
    profile = ValidationProfile.model_validate(payload)
    assert profile.inference_targets[0].expected_image.endswith("a" * 64)


def test_validation_profile_requires_unique_target_ids_across_namespaces() -> None:
    payload = profile_payload()
    payload["inference_targets"][0]["id"] = "evo-x2-2"  # type: ignore[index]

    with pytest.raises(ValidationError, match="target IDs overlap"):
        ValidationProfile.model_validate(payload)


@pytest.mark.parametrize("forbidden_field", ["markers", "suite_env", "engine_args"])
def test_validation_profile_rejects_test_and_engine_selectors(
    forbidden_field: str,
) -> None:
    payload = profile_payload()
    payload["validation"][forbidden_field] = "not requires_two_clusters"  # type: ignore[index]

    with pytest.raises(ValidationError, match=forbidden_field):
        ValidationProfile.model_validate(payload)


@pytest.mark.parametrize("required", [True, False])
def test_resolved_scenario_rejects_zero_cases(required: bool) -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        ResolvedScenario(
            target_id="evo-x2-2-llamacpp-chat",
            cluster_id="evo-x2-2",
            scenario_id="sdk.inference.lifecycle/v1",
            required=required,
            case_ids=(),
            redacted_parameters={},
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_protocol_models_reject_non_finite_json_values(value: float) -> None:
    with pytest.raises(ValidationError):
        ResolvedScenario(
            target_id="evo-x2-2-llamacpp-chat",
            cluster_id="evo-x2-2",
            scenario_id="sdk.inference.lifecycle/v1",
            required=True,
            case_ids=("openai-multi-turn-chat",),
            redacted_parameters={"threshold": value},
        )


def test_scenario_catalog_rejects_empty_and_duplicate_scenario_ids() -> None:
    descriptor = GoldenProvider().describe()[0]

    with pytest.raises(ValidationError, match="at least one descriptor"):
        ScenarioCatalog(())
    with pytest.raises(ValidationError, match="duplicate scenario ID"):
        ScenarioCatalog((descriptor, descriptor))


def test_matcher_path_is_an_unambiguous_segment_array() -> None:
    descriptor = GoldenProvider().describe()[0]

    assert descriptor.model_dump(mode="json")["applies_when"][0]["path"] == [
        "target",
        "engine",
    ]
    payload = descriptor.model_dump(mode="json")
    payload["applies_when"][0]["path"] = "target.engine"
    with pytest.raises(ValidationError, match="path"):
        ScenarioDescriptor.model_validate(payload)


@pytest.mark.parametrize("field", ["target_scope", "minimum_level"])
def test_scenario_descriptor_requires_activation_metadata(field: str) -> None:
    payload = GoldenProvider().describe()[0].model_dump(mode="python")
    del payload[field]

    with pytest.raises(ValidationError, match=field):
        ScenarioDescriptor.model_validate(payload)


def test_validation_profile_rejects_mesh_edges_to_unknown_clusters() -> None:
    payload = profile_payload()
    payload["mesh"] = {"edges": [_mesh_edge(receiver="missing-cluster")]}

    with pytest.raises(ValidationError, match="mesh edges reference unknown clusters"):
        ValidationProfile.model_validate(payload)


def test_validation_profile_rejects_self_and_duplicate_mesh_edges() -> None:
    self_edge = profile_payload()
    self_edge["mesh"] = {"edges": [_mesh_edge(receiver="evo-x2-2")]}
    with pytest.raises(ValidationError, match="distinct clusters"):
        ValidationProfile.model_validate(self_edge)

    duplicate = profile_payload()
    edge = _mesh_edge(receiver="peer-cluster")
    duplicate["clusters"].append(  # type: ignore[union-attr]
        {
            "id": "peer-cluster",
            "roles": ["controller"],
            "node_count": 1,
            "hardware": {"accelerators": []},
            "features": {},
        }
    )
    duplicate["mesh"] = {"edges": [edge, edge]}
    with pytest.raises(ValidationError, match="duplicate"):
        ValidationProfile.model_validate(duplicate)


def test_mesh_edge_target_id_is_stable_and_profile_namespaces_are_disjoint() -> None:
    payload = profile_payload()
    payload["clusters"].append(  # type: ignore[union-attr]
        {
            "id": "peer-cluster",
            "roles": ["controller"],
            "node_count": 1,
            "hardware": {"accelerators": []},
            "features": {},
        }
    )
    payload["mesh"] = {"edges": [_mesh_edge(receiver="peer-cluster")]}  # type: ignore[index]
    profile = ValidationProfile.model_validate(payload)
    edge = profile.mesh.edges[0]

    assert mesh_edge_target_id(edge) == mesh_edge_target_id(edge)
    assert mesh_edge_target_id(edge).startswith("mesh-edge:sha256:")
    assert mesh_edge_target_id(edge) not in {
        cluster.id for cluster in profile.clusters
    }


def test_mesh_edge_plan_binds_both_endpoint_clusters() -> None:
    payload = profile_payload()
    payload["clusters"].append(  # type: ignore[union-attr]
        {
            "id": "peer-cluster",
            "roles": ["controller"],
            "node_count": 1,
            "hardware": {"accelerators": []},
            "features": {},
        }
    )
    payload["mesh"] = {"edges": [_mesh_edge(receiver="peer-cluster")]}  # type: ignore[index]
    profile = ValidationProfile.model_validate(payload)
    edge = profile.mesh.edges[0]
    selected = ResolvedScenario(
        target_id=mesh_edge_target_id(edge),
        cluster_id=edge.initiator,
        scenario_id="sdk.federation.shared-idp/v1",
        required=True,
        case_ids=("pairing",),
        redacted_parameters={},
        cluster_ids=(edge.initiator, edge.receiver),
    )
    from kamiwaza_sdk.validation import ScenarioPlan

    plan = ScenarioPlan(
        schema="kamiwaza.scenario-plan/v1",
        profile_digest=model_digest(profile),
        provider_revision="sdk.federation.shared-idp@v1",
        selected=(selected,),
        install_requirements={},
        runtime_requirements=(),
    )

    validate_plan_identity(profile, plan)
    runtime = RuntimeContext.model_validate(
        {
            "schema": "kamiwaza.runtime-context/v1",
            "run_id": "run-123",
            "clusters": [
                {
                    "id": edge.initiator,
                    "base_url": "https://initiator.example.test/api",
                    "api_key_ref": "secret://initiator/admin-pat",
                    "kubeconfig_ref": "file:///run/secrets/initiator.kubeconfig",
                },
                {
                    "id": edge.receiver,
                    "base_url": "https://receiver.example.test/api",
                    "api_key_ref": "secret://receiver/admin-pat",
                    "kubeconfig_ref": "file:///run/secrets/receiver.kubeconfig",
                },
            ],
        }
    )
    validate_plan_runtime_identity(plan, runtime)

    with pytest.raises(ProviderContractError, match="target cluster binding"):
        validate_plan_identity(
            profile,
            plan.model_copy(
                update={"selected": (selected.model_copy(update={"cluster_ids": ()}),)}
            ),
        )
    with pytest.raises(ProviderContractError, match="runtime missing"):
        validate_plan_runtime_identity(
            plan,
            runtime.model_copy(update={"clusters": runtime.clusters[:1]}),
        )


def test_runtime_context_rejects_inline_secrets_and_hides_references() -> None:
    cluster = {
        "id": "evo-x2-2",
        "base_url": "https://evo-x2-2.example.test/api",
        "api_key_ref": "secret://evo-x2-2/admin-pat",
        "kubeconfig_ref": "file:///run/secrets/evo-x2-2.kubeconfig",
    }
    runtime = RuntimeContext.model_validate(
        {
            "schema": "kamiwaza.runtime-context/v1",
            "run_id": "run-123",
            "ownership_key_ref": "secret://run-123/ownership-key",
            "clusters": [cluster],
        }
    )

    assert "admin-pat" not in repr(runtime)
    assert "ownership-key" not in repr(runtime)
    cluster["api_key"] = "inline-secret"
    with pytest.raises(ValidationError, match="api_key"):
        RuntimeContext.model_validate(
            {
                "schema": "kamiwaza.runtime-context/v1",
                "run_id": "run-123",
                "clusters": [cluster],
            }
        )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("ownership_key_ref", "secret://"),
        ("ownership_key_ref", "secret://ownership-key\nforged"),
        ("base_url", "https://"),
        ("base_url", "https://cluster.example.test/api\nforged"),
        ("api_key_ref", "secret://"),
        ("api_key_ref", "secret://token\nforged"),
        ("kubeconfig_ref", "file://"),
        ("kubeconfig_ref", "file:///secret\nforged"),
    ],
)
def test_runtime_context_rejects_empty_or_multiline_references(
    field: str, invalid_value: str
) -> None:
    payload = {
        "schema": "kamiwaza.runtime-context/v1",
        "run_id": "run-123",
        "clusters": [
            {
                "id": "evo-x2-2",
                "base_url": "https://evo-x2-2.example.test/api",
                "api_key_ref": "secret://evo-x2-2/admin-pat",
                "kubeconfig_ref": "file:///run/secrets/evo-x2-2.kubeconfig",
            }
        ],
    }
    if field == "ownership_key_ref":
        payload[field] = invalid_value
    else:
        payload["clusters"][0][field] = invalid_value  # type: ignore[index]

    with pytest.raises(ValidationError, match=field):
        RuntimeContext.model_validate(payload)


@pytest.mark.parametrize(
    ("cleanup_status", "result_status", "expected_error"),
    [
        ("passed", "failed", "passed cleanup contains a failure"),
        ("failed", "removed", "failed cleanup contains no failure"),
    ],
)
def test_cleanup_evidence_status_matches_result_inventory(
    cleanup_status: Literal["passed", "failed"],
    result_status: Literal["removed", "failed"],
    expected_error: str,
) -> None:
    with pytest.raises(ValidationError, match=expected_error):
        CleanupEvidence(
            schema="kamiwaza.cleanup-evidence/v1",
            provider_revision="sdk.golden@v1",
            run_id="run-123",
            state_digest="sha256:" + "1" * 64,
            status=cleanup_status,
            results=(
                CleanupResult(
                    target_id="evo-x2-2-llamacpp-chat",
                    resource_type="deployment",
                    resource_id="owned-deployment",
                    status=result_status,
                    detail="resource remains",
                ),
            ),
        )


def test_coverage_summary_status_must_match_its_issue_inventory() -> None:
    with pytest.raises(ValidationError, match="passed coverage contains issues"):
        CoverageSummary(
            schema="kamiwaza.coverage-summary/v1",
            status="passed",
            plan_digest="sha256:" + "2" * 64,
            issues=(
                CoverageIssue(
                    code="missing_case",
                    target_id="evo-x2-2-llamacpp-chat",
                    scenario_id="sdk.inference.lifecycle/v1",
                    case_id="openai-multi-turn-chat",
                    detail="planned case has no evidence",
                ),
            ),
        )

    with pytest.raises(ValidationError, match="failed coverage has no issues"):
        CoverageSummary(
            schema="kamiwaza.coverage-summary/v1",
            status="failed",
            plan_digest="sha256:" + "2" * 64,
            issues=(),
        )


def test_coverage_summary_requires_explicit_wire_version() -> None:
    with pytest.raises(ValidationError, match="schema"):
        CoverageSummary.model_validate(
            {
                "status": "passed",
                "plan_digest": "sha256:" + "2" * 64,
                "issues": [],
            }
        )
