"""Closed, versioned models for cross-repository validation providers."""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    model_validator,
)

Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
StableId = Annotated[
    str,
    Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"),
]
NonEmptyText = Annotated[str, Field(min_length=1, max_length=4096)]
ImmutableImageReference = Annotated[
    str,
    Field(
        max_length=4096,
        pattern=r"^(?:[^@\s]+@)?sha256:[0-9a-f]{64}$",
    ),
]


def _require_values(values: Sequence[object], message: str) -> None:
    if not values:
        raise ValueError(message)


def _require_values_when(
    values: Sequence[object], required: bool, message: str
) -> None:
    if required and not values:
        raise ValueError(message)


def _reject_duplicate_values(values: Sequence[Hashable], message: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(message)


def _reject_unknown_references(
    references: set[str], known: set[str], label: str
) -> None:
    missing = references - known
    if missing:
        raise ValueError(f"{label} reference unknown clusters: {missing}")


class ClosedModel(BaseModel):
    """Immutable model that rejects fields outside the versioned contract."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class DeploymentFacts(ClosedModel):
    provider: StableId
    topology_id: StableId
    ephemeral: bool


class AcceleratorFacts(ClosedModel):
    vendor: StableId
    architecture: StableId
    count: int = Field(ge=1)


class HardwareFacts(ClosedModel):
    accelerators: tuple[AcceleratorFacts, ...] = ()


class ClusterFacts(ClosedModel):
    id: StableId
    roles: Annotated[
        tuple[StableId, ...],
        Field(min_length=1, json_schema_extra={"uniqueItems": True}),
    ]
    node_count: int = Field(ge=1)
    hardware: HardwareFacts
    features: dict[StableId, bool] = Field(
        default_factory=dict,
        json_schema_extra={
            "propertyNames": {
                "maxLength": 256,
                "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
            }
        },
    )

    @model_validator(mode="after")
    def validate_roles(self) -> ClusterFacts:
        _require_values(self.roles, "cluster must declare at least one role")
        _reject_duplicate_values(self.roles, "cluster roles contain a duplicate")
        return self


class MeshEdge(ClosedModel):
    initiator: StableId
    receiver: StableId
    identity_mode: StableId

    @model_validator(mode="after")
    def validate_endpoints(self) -> MeshEdge:
        if self.initiator == self.receiver:
            raise ValueError("mesh edge must connect distinct clusters")
        return self


class MeshFacts(ClosedModel):
    edges: Annotated[
        tuple[MeshEdge, ...], Field(json_schema_extra={"uniqueItems": True})
    ] = ()

    @model_validator(mode="after")
    def validate_edges(self) -> MeshFacts:
        keys = [
            (edge.initiator, edge.receiver, edge.identity_mode) for edge in self.edges
        ]
        _reject_duplicate_values(keys, "mesh contains a duplicate edge")
        return self


class ValidationIntent(ClosedModel):
    level: Literal["smoke", "standard", "comprehensive"]
    fixture_mode: Literal["owned", "external"]
    include: Annotated[
        tuple[StableId, ...], Field(json_schema_extra={"uniqueItems": True})
    ] = ()
    exclude: Annotated[
        tuple[StableId, ...], Field(json_schema_extra={"uniqueItems": True})
    ] = ()

    @model_validator(mode="after")
    def validate_scenario_overrides(self) -> ValidationIntent:
        if set(self.include) & set(self.exclude):
            raise ValueError("validation include and exclude overlap")
        _reject_duplicate_values(
            self.include, "validation include IDs contain a duplicate"
        )
        _reject_duplicate_values(
            self.exclude, "validation exclude IDs contain a duplicate"
        )
        return self


class InferenceTarget(ClosedModel):
    id: StableId
    cluster_id: StableId
    required: bool
    repository: NonEmptyText
    engine: StableId
    model_format: StableId
    quantization: StableId
    runtime_profile: StableId
    expected_image: ImmutableImageReference | None = None


class ValidationProfile(ClosedModel):
    schema_id: Literal["kamiwaza.validation-profile/v1"] = Field(alias="schema")
    deployment: DeploymentFacts
    clusters: Annotated[
        tuple[ClusterFacts, ...],
        Field(min_length=1, json_schema_extra={"uniqueItems": True}),
    ]
    mesh: MeshFacts = Field(default_factory=MeshFacts)
    validation: ValidationIntent
    inference_targets: Annotated[
        tuple[InferenceTarget, ...], Field(json_schema_extra={"uniqueItems": True})
    ] = ()

    @model_validator(mode="after")
    def validate_references(self) -> ValidationProfile:
        cluster_ids = [cluster.id for cluster in self.clusters]
        target_ids = [target.id for target in self.inference_targets]
        known_clusters = set(cluster_ids)
        _require_values(cluster_ids, "profile clusters must not be empty")
        _reject_duplicate_values(cluster_ids, "profile cluster IDs contain a duplicate")
        _reject_duplicate_values(
            target_ids, "profile inference target IDs contain a duplicate"
        )
        _reject_unknown_references(
            {target.cluster_id for target in self.inference_targets},
            known_clusters,
            "inference targets",
        )
        edge_cluster_ids = {
            endpoint
            for edge in self.mesh.edges
            for endpoint in (edge.initiator, edge.receiver)
        }
        _reject_unknown_references(edge_cluster_ids, known_clusters, "mesh edges")
        return self


class FactMatcher(ClosedModel):
    path: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+$")]
    operator: Literal["eq", "in", "contains", "gte", "lte"]
    value: JsonValue


class ScenarioDescriptor(ClosedModel):
    scenario_id: StableId
    provider_id: StableId
    protocol_version: Literal["v1"]
    capability_ids: tuple[StableId, ...]
    applies_when: tuple[FactMatcher, ...]
    requires: tuple[StableId, ...]
    fixture_modes: tuple[Literal["owned", "external"], ...]
    case_ids: Annotated[
        tuple[StableId, ...],
        Field(min_length=1, json_schema_extra={"uniqueItems": True}),
    ]

    @model_validator(mode="after")
    def validate_case_registry(self) -> ScenarioDescriptor:
        _require_values(self.case_ids, "scenario descriptor case must not be empty")
        _reject_duplicate_values(
            self.case_ids, "scenario descriptor case IDs contain a duplicate"
        )
        return self


class ScenarioCatalog(RootModel[tuple[ScenarioDescriptor, ...]]):
    """Wire document emitted by the deterministic describe command."""

    model_config = ConfigDict(frozen=True, json_schema_extra={"minItems": 1})

    @model_validator(mode="after")
    def validate_descriptors(self) -> ScenarioCatalog:
        scenario_ids = [descriptor.scenario_id for descriptor in self.root]
        _require_values(scenario_ids, "scenario catalog needs at least one descriptor")
        _reject_duplicate_values(
            scenario_ids, "scenario catalog contains a duplicate scenario ID"
        )
        return self


class ResolvedScenario(ClosedModel):
    target_id: StableId
    scenario_id: StableId
    required: bool
    case_ids: Annotated[
        tuple[StableId, ...], Field(json_schema_extra={"uniqueItems": True})
    ]
    redacted_parameters: dict[str, JsonValue]

    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "properties": {"required": {"const": True}},
                        "required": ["required"],
                    },
                    "then": {"properties": {"case_ids": {"minItems": 1}}},
                }
            ]
        }
    )

    @model_validator(mode="after")
    def validate_cases(self) -> ResolvedScenario:
        _require_values_when(
            self.case_ids,
            self.required,
            "required scenario must select at least one case",
        )
        _reject_duplicate_values(
            self.case_ids, "resolved scenario case IDs contain a duplicate"
        )
        return self


class ScenarioPlan(ClosedModel):
    schema_id: Literal["kamiwaza.scenario-plan/v1"] = Field(alias="schema")
    profile_digest: Digest
    provider_revision: NonEmptyText
    selected: Annotated[
        tuple[ResolvedScenario, ...], Field(json_schema_extra={"uniqueItems": True})
    ]
    install_requirements: dict[str, JsonValue]
    runtime_requirements: tuple[StableId, ...]

    @model_validator(mode="after")
    def validate_selected_scenarios(self) -> ScenarioPlan:
        keys = [(item.target_id, item.scenario_id) for item in self.selected]
        _reject_duplicate_values(
            keys,
            "scenario plan contains a duplicate target/scenario cell",
        )
        return self


class CaseResult(ClosedModel):
    target_id: StableId
    scenario_id: StableId
    case_id: StableId
    status: Literal["passed", "failed", "skipped"]
    duration_ms: int = Field(ge=0)
    detail: str | None = Field(default=None, max_length=4096)


class ScenarioEvidence(ClosedModel):
    schema_id: Literal["kamiwaza.scenario-evidence/v1"] = Field(alias="schema")
    provider_revision: NonEmptyText
    profile_digest: Digest
    plan_digest: Digest
    state_digest: Digest
    results: tuple[CaseResult, ...]
    resolved_runtime: dict[str, JsonValue]


class RuntimeCluster(ClosedModel):
    id: StableId
    base_url: Annotated[
        str,
        Field(
            pattern=r"^https?://[^\s/]+(?:/[^\s]*)?$",
            max_length=2048,
        ),
    ]
    api_key_ref: Annotated[
        str,
        Field(
            pattern=r"^(?:secret|file)://[^\s]+$",
            max_length=4096,
            repr=False,
        ),
    ]
    kubeconfig_ref: Annotated[
        str,
        Field(pattern=r"^file://[^\s]+$", max_length=4096, repr=False),
    ]


class RuntimeContext(ClosedModel):
    schema_id: Literal["kamiwaza.runtime-context/v1"] = Field(alias="schema")
    run_id: StableId
    clusters: Annotated[
        tuple[RuntimeCluster, ...],
        Field(min_length=1, json_schema_extra={"uniqueItems": True}),
    ]

    @model_validator(mode="after")
    def validate_clusters(self) -> RuntimeContext:
        cluster_ids = [cluster.id for cluster in self.clusters]
        _require_values(
            cluster_ids, "runtime context must declare at least one cluster"
        )
        _reject_duplicate_values(cluster_ids, "runtime cluster IDs contain a duplicate")
        return self


class FixtureMutation(ClosedModel):
    sequence: int = Field(ge=1)
    target_id: StableId
    resource_type: StableId
    resource_id: NonEmptyText
    action: Literal["created", "adopted", "removed"]


class FixtureState(ClosedModel):
    schema_id: Literal["kamiwaza.fixture-state/v1"] = Field(alias="schema")
    provider_revision: NonEmptyText
    plan_digest: Digest
    runtime_digest: Digest
    run_id: StableId
    owner_token_digest: Digest
    journal: tuple[FixtureMutation, ...]
    opaque: dict[str, JsonValue] = Field(default_factory=dict, repr=False)

    @model_validator(mode="after")
    def validate_journal(self) -> FixtureState:
        expected = tuple(range(1, len(self.journal) + 1))
        actual = tuple(item.sequence for item in self.journal)
        if actual != expected:
            raise ValueError("fixture journal sequence must be contiguous from one")
        return self


class CleanupResult(ClosedModel):
    target_id: StableId
    resource_type: StableId
    resource_id: NonEmptyText
    status: Literal["removed", "absent", "retained_foreign", "failed"]
    detail: str | None = Field(default=None, max_length=4096)


class CleanupEvidence(ClosedModel):
    schema_id: Literal["kamiwaza.cleanup-evidence/v1"] = Field(alias="schema")
    provider_revision: NonEmptyText
    run_id: StableId
    state_digest: Digest
    status: Literal["passed", "failed"]
    results: tuple[CleanupResult, ...]

    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "properties": {"status": {"const": "passed"}},
                        "required": ["status"],
                    },
                    "then": {
                        "properties": {
                            "results": {
                                "not": {
                                    "contains": {
                                        "properties": {"status": {"const": "failed"}},
                                        "required": ["status"],
                                    }
                                }
                            }
                        }
                    },
                    "else": {
                        "properties": {
                            "results": {
                                "contains": {
                                    "properties": {"status": {"const": "failed"}},
                                    "required": ["status"],
                                }
                            }
                        }
                    },
                }
            ]
        }
    )

    @model_validator(mode="after")
    def validate_status(self) -> CleanupEvidence:
        contains_failure = any(result.status == "failed" for result in self.results)
        if self.status == "passed" and contains_failure:
            raise ValueError("passed cleanup contains a failure")
        if self.status == "failed" and not contains_failure:
            raise ValueError("failed cleanup contains no failure")
        return self


CoverageIssueCode = Literal[
    "metadata_mismatch",
    "missing_case",
    "unexpected_case",
    "duplicate_case",
    "required_failure",
    "required_skip",
]


class CoverageIssue(ClosedModel):
    code: CoverageIssueCode
    target_id: StableId | None = None
    scenario_id: StableId | None = None
    case_id: StableId | None = None
    detail: NonEmptyText


class CoverageSummary(ClosedModel):
    schema_id: Literal["kamiwaza.coverage-summary/v1"] = Field(alias="schema")
    status: Literal["passed", "failed"]
    plan_digest: Digest
    issues: tuple[CoverageIssue, ...]

    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "properties": {"status": {"const": "passed"}},
                        "required": ["status"],
                    },
                    "then": {"properties": {"issues": {"maxItems": 0}}},
                    "else": {"properties": {"issues": {"minItems": 1}}},
                }
            ]
        }
    )

    @model_validator(mode="after")
    def validate_status(self) -> CoverageSummary:
        if self.status == "passed":
            if self.issues:
                raise ValueError("passed coverage contains issues")
        elif not self.issues:
            raise ValueError("failed coverage has no issues")
        return self
