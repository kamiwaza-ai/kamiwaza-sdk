"""Static contract and resolution for federated model-mesh validation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from kamiwaza_sdk.validation.applicability import ApplicableTarget
from kamiwaza_sdk.validation.inference_spec import _compatibility_error
from kamiwaza_sdk.validation.federation_spec import (
    SHARED_REALM_CLIENT_ID,
    planned_shared_issuer,
)
from kamiwaza_sdk.validation.models import (
    FactMatcher,
    InferenceTarget,
    ResolvedScenario,
    ScenarioDescriptor,
    ValidationProfile,
)
from kamiwaza_sdk.validation.provider import ProviderContractError

MODEL_MESH_PROVIDER_ID = "sdk.federation.model-mesh"
MODEL_MESH_PROVIDER_REVISION = "sdk.federation.model-mesh@v1"
MODEL_MESH_SCENARIO_ID = "sdk.federation.model-mesh/v1"

# The inventory is deliberately small and exact.  Discovery proves the
# receiver-side model grant, chat proves the remote runtime path, and the
# negative case proves an allowlisted identity does not inherit model access.
MODEL_MESH_CASE_IDS = (
    "model-grant-authorized",
    "model-discovery-stable-identity",
    "model-remote-chat-provenance",
    "model-unauthorized-negative",
)


@dataclass(frozen=True)
class _ResolutionInputs:
    clusters: dict[str, Any]
    targets: Sequence[InferenceTarget]
    shared_issuer: str
    realm: str


def scenario_descriptor() -> ScenarioDescriptor:
    """Describe one required model-mesh scenario per shared-IdP edge."""

    return ScenarioDescriptor(
        scenario_id=MODEL_MESH_SCENARIO_ID,
        provider_id=MODEL_MESH_PROVIDER_ID,
        protocol_version="v1",
        target_scope="mesh_edge",
        minimum_level="standard",
        capability_ids=(
            "federation.model-grants",
            "federation.remote-model-discovery",
            "federation.remote-model-chat",
        ),
        applies_when=(
            FactMatcher(
                path=("edge", "identity_mode"),
                operator="eq",
                value="shared_idp",
            ),
        ),
        requires=("cluster-api", "kube-api", "ownership-key"),
        fixture_modes=("owned",),
        case_ids=MODEL_MESH_CASE_IDS,
    )


def resolve_candidates(
    profile: ValidationProfile,
    candidates: Sequence[ApplicableTarget],
    *,
    explicit: bool,
) -> tuple[ResolvedScenario, ...]:
    """Bind each shared-IdP edge to one deterministic receiver model target.

    Mesh-edge applicability intentionally remains independent from inference
    target applicability: an edge has two endpoint clusters, while a model
    target belongs to exactly one cluster.  Resolution is therefore the seam
    that joins the receiver endpoint to the M2-qualified target without
    widening the protocol's target identity.
    """

    if not candidates:
        if explicit:
            raise ProviderContractError(
                "requested model-mesh scenario has no compatible mesh edge"
            )
        return ()

    clusters = {item.id: item for item in profile.clusters}
    targets = tuple(profile.inference_targets)
    shared_issuer = planned_shared_issuer(profile)
    realm = shared_issuer.rsplit("/", 1)[-1]
    inputs = _ResolutionInputs(clusters, targets, shared_issuer, realm)
    return tuple(_resolve_candidate(candidate, inputs) for candidate in candidates)


def _resolve_candidate(
    candidate: ApplicableTarget,
    inputs: _ResolutionInputs,
) -> ResolvedScenario:
    if len(candidate.cluster_ids) != 2:
        raise ProviderContractError("model-mesh edge must bind both endpoint clusters")
    receiver_id = candidate.cluster_ids[1]
    receiver = inputs.clusters.get(receiver_id)
    if receiver is None:
        raise ProviderContractError("model-mesh edge receiver cluster is undeclared")
    target = _select_receiver_target(inputs.targets, receiver_id, receiver)
    if target is None:
        raise ProviderContractError(
            f"no compatible model target for mesh edge receiver {receiver_id}"
        )
    return _resolved_candidate(candidate, target, inputs.shared_issuer, inputs.realm)


def _select_receiver_target(
    targets: Sequence[InferenceTarget], receiver_id: str, receiver: Any
) -> InferenceTarget | None:
    compatible = tuple(
        target for target in targets if _target_matches(target, receiver_id, receiver)
    )
    return min(compatible, key=lambda item: item.id) if compatible else None


def _target_matches(target: InferenceTarget, receiver_id: str, receiver: Any) -> bool:
    if target.cluster_id != receiver_id:
        return False
    return _compatibility_error(target, receiver) is None


def _resolved_candidate(
    candidate: ApplicableTarget,
    target: InferenceTarget,
    shared_issuer: str,
    realm: str,
) -> ResolvedScenario:
    if len(candidate.cluster_ids) != 2:
        raise ProviderContractError("model-mesh edge must bind both endpoint clusters")
    return ResolvedScenario(
        target_id=candidate.target_id,
        cluster_id=candidate.cluster_id,
        cluster_ids=candidate.cluster_ids,
        scenario_id=MODEL_MESH_SCENARIO_ID,
        required=candidate.required,
        case_ids=MODEL_MESH_CASE_IDS,
        redacted_parameters={
            "issuer": shared_issuer,
            "realm": realm,
            "client_id": SHARED_REALM_CLIENT_ID,
            "persona_usernames": [
                "fed-clr-u",
                "fed-clr-s",
                "fed-clr-ts",
                "fed-clr-unonboarded",
                "fed-tenant-missing",
                "fed-tenant-legacy-only",
                "fed-tenant-nondefault",
            ],
            "model_target_id": target.id,
            "model_repository": target.repository,
            "model_engine": target.engine,
            "model_format": target.model_format,
            "model_quantization": target.quantization,
            "model_runtime_profile": target.runtime_profile,
            "model_expected_image": target.expected_image,
            "fixture_mode": "owned",
        },
    )


def model_target_parameters(selected: ResolvedScenario) -> dict[str, Any]:
    """Return the non-secret model target inputs carried by a selection."""

    values = selected.redacted_parameters
    required = (
        "model_target_id",
        "model_repository",
        "model_engine",
        "model_format",
        "model_quantization",
        "model_runtime_profile",
    )
    missing = [key for key in required if not isinstance(values.get(key), str)]
    if missing:
        raise ProviderContractError(
            f"model-mesh selection is missing target parameters: {', '.join(missing)}"
        )
    return dict(values)
