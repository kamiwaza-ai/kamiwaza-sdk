"""Contract tests for the closed scenario-matcher fact language."""

from __future__ import annotations

import pytest

from kamiwaza_sdk.validation import FactMatcher, ValidationProfile
from kamiwaza_sdk.validation.applicability import applicable_targets
from kamiwaza_sdk.validation.golden_provider import GoldenProvider
from kamiwaza_sdk.validation.provider import (
    ProviderContractError,
    validate_plan_completeness,
)

from .support import profile_payload

pytestmark = pytest.mark.contract


def _profile() -> ValidationProfile:
    payload = profile_payload()
    payload["validation"]["include"] = ["sdk.golden.echo/v1"]  # type: ignore[index]
    return ValidationProfile.model_validate(payload)


def test_optional_null_fact_evaluates_as_non_matching_when_excluded() -> None:
    source_profile = _profile()
    profile = source_profile.model_copy(
        update={
            "validation": source_profile.validation.model_copy(
                update={"include": (), "exclude": ("sdk.golden.echo/v1",)}
            )
        }
    )
    source_descriptor = GoldenProvider().describe()[0]
    descriptor = source_descriptor.model_copy(
        update={
            "applies_when": (
                source_descriptor.applies_when[0].model_copy(
                    update={
                        "path": ("target", "expected_image"),
                        "operator": "contains",
                        "value": "sha256:",
                    }
                ),
            ),
        }
    )
    empty_plan = GoldenProvider().resolve(source_profile).model_copy(
        update={"selected": ()}
    )

    validate_plan_completeness(profile, (descriptor,), empty_plan)


@pytest.mark.parametrize(
    ("expected_image", "matches"),
    [(None, False), ("sha256:" + "a" * 64, True)],
)
def test_contains_matches_only_a_populated_optional_string(
    expected_image: str | None, matches: bool
) -> None:
    source_profile = _profile()
    target = source_profile.inference_targets[0].model_copy(
        update={"expected_image": expected_image}
    )
    profile = source_profile.model_copy(update={"inference_targets": (target,)})
    source_descriptor = GoldenProvider().describe()[0]
    matcher = source_descriptor.applies_when[0].model_copy(
        update={
            "path": ("target", "expected_image"),
            "operator": "contains",
            "value": "sha256:",
        }
    )
    descriptor = source_descriptor.model_copy(update={"applies_when": (matcher,)})

    applicable = applicable_targets(profile, descriptor)

    assert bool(applicable) is matches


@pytest.mark.parametrize("feature_id", ["gpu.vendor", "gpu/vendor:v1"])
def test_matcher_path_segments_address_every_valid_feature_key(
    feature_id: str,
) -> None:
    source_profile = _profile()
    cluster = source_profile.clusters[0].model_copy(
        update={"features": {feature_id: True}}
    )
    profile = source_profile.model_copy(update={"clusters": (cluster,)})
    source_descriptor = GoldenProvider().describe()[0]
    matcher = source_descriptor.applies_when[0].model_copy(
        update={
            "path": ("cluster", "features", feature_id),
            "operator": "eq",
            "value": True,
        }
    )
    descriptor = source_descriptor.model_copy(
        update={"target_scope": "cluster", "applies_when": (matcher,)}
    )

    assert [target.target_id for target in applicable_targets(profile, descriptor)] == [
        cluster.id
    ]


def test_mesh_edge_scope_matches_edge_facts_and_binds_both_clusters() -> None:
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
    payload["mesh"] = {  # type: ignore[index]
        "edges": [
            {
                "initiator": "evo-x2-2",
                "receiver": "peer-cluster",
                "identity_mode": "shared_idp",
            }
        ]
    }
    profile = ValidationProfile.model_validate(payload)
    descriptor = GoldenProvider().describe()[0].model_copy(
        update={
            "scenario_id": "sdk.federation.shared-idp/v1",
            "target_scope": "mesh_edge",
            "applies_when": (
                FactMatcher(
                    path=("edge", "identity_mode"),
                    operator="eq",
                    value="shared_idp",
                ),
            ),
        }
    )

    matches = applicable_targets(profile, descriptor)

    assert len(matches) == 1
    assert matches[0].cluster_ids == ("evo-x2-2", "peer-cluster")
    assert matches[0].cluster_id == "evo-x2-2"


def test_mesh_edge_scope_rejects_single_cluster_matcher_root() -> None:
    descriptor = GoldenProvider().describe()[0].model_copy(
        update={
            "target_scope": "mesh_edge",
            "applies_when": (
                GoldenProvider().describe()[0].applies_when[0].model_copy(
                    update={
                        "path": ("cluster", "roles"),
                        "operator": "contains",
                        "value": "controller",
                    }
                ),
            ),
        }
    )

    with pytest.raises(ProviderContractError, match="invalid fact root"):
        applicable_targets(_profile(), descriptor)


@pytest.mark.parametrize(
    ("target_scope", "path", "value"),
    [
        ("cluster", ("cluster", "features", "-"), True),
        ("inference_target", ("target", "engine"), " "),
        ("cluster", ("cluster", "node_count"), -1),
    ],
)
def test_matcher_compiler_rejects_unrepresentable_paths_and_values(
    target_scope: str, path: tuple[str, ...], value: object
) -> None:
    source_descriptor = GoldenProvider().describe()[0]
    matcher = source_descriptor.applies_when[0].model_copy(
        update={"path": path, "operator": "eq", "value": value}
    )
    descriptor = source_descriptor.model_copy(
        update={"target_scope": target_scope, "applies_when": (matcher,)}
    )

    with pytest.raises(ProviderContractError, match="incompatible value types"):
        applicable_targets(_profile(), descriptor)
