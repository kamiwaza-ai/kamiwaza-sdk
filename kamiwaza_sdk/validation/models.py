"""Closed, versioned models for cross-repository validation providers."""

from __future__ import annotations

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


def _require_ids(values: list[str], label: str) -> None:
    if not values:
        raise ValueError(f"{label} must not be empty")


def _reject_duplicate_ids(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} IDs contain a duplicate")


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
    roles: tuple[StableId, ...]
    node_count: int = Field(ge=1)
    hardware: HardwareFacts
    features: dict[StableId, bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_roles(self) -> ClusterFacts:
        if not self.roles:
            raise ValueError("cluster must declare at least one role")
        if len(self.roles) != len(set(self.roles)):
            raise ValueError("cluster roles contain a duplicate")
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
    edges: tuple[MeshEdge, ...] = ()

    @model_validator(mode="after")
    def validate_edges(self) -> MeshFacts:
        keys = [
            (edge.initiator, edge.receiver, edge.identity_mode) for edge in self.edges
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("mesh contains a duplicate edge")
        return self


class ValidationIntent(ClosedModel):
    level: Literal["smoke", "standard", "comprehensive"]
    fixture_mode: Literal["owned", "external"]
    include: tuple[StableId, ...] = ()
    exclude: tuple[StableId, ...] = ()

    @model_validator(mode="after")
    def validate_scenario_overrides(self) -> ValidationIntent:
        if set(self.include) & set(self.exclude):
            raise ValueError("validation include and exclude overlap")
        if len(self.include) != len(set(self.include)):
            raise ValueError("validation include contains a duplicate")
        if len(self.exclude) != len(set(self.exclude)):
            raise ValueError("validation exclude contains a duplicate")
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
    clusters: tuple[ClusterFacts, ...]
    mesh: MeshFacts = Field(default_factory=MeshFacts)
    validation: ValidationIntent
    inference_targets: tuple[InferenceTarget, ...] = ()

    @model_validator(mode="after")
    def validate_references(self) -> ValidationProfile:
        cluster_ids = [cluster.id for cluster in self.clusters]
        target_ids = [target.id for target in self.inference_targets]
        known_clusters = set(cluster_ids)
        _require_ids(cluster_ids, "profile clusters")
        _reject_duplicate_ids(cluster_ids, "profile cluster")
        _reject_duplicate_ids(target_ids, "profile inference target")
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
    case_ids: tuple[StableId, ...]

    @model_validator(mode="after")
    def validate_case_registry(self) -> ScenarioDescriptor:
        if not self.case_ids:
            raise ValueError("scenario descriptor must declare at least one case")
        if len(self.case_ids) != len(set(self.case_ids)):
            raise ValueError("scenario descriptor case IDs contain a duplicate")
        return self


class ScenarioCatalog(RootModel[tuple[ScenarioDescriptor, ...]]):
    """Wire document emitted by the deterministic describe command."""

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="after")
    def validate_descriptors(self) -> ScenarioCatalog:
        if not self.root:
            raise ValueError("scenario catalog needs at least one descriptor")
        scenario_ids = [descriptor.scenario_id for descriptor in self.root]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("scenario catalog contains a duplicate scenario ID")
        return self


class ResolvedScenario(ClosedModel):
    target_id: StableId
    scenario_id: StableId
    required: bool
    case_ids: tuple[StableId, ...]
    redacted_parameters: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_cases(self) -> ResolvedScenario:
        if self.required and not self.case_ids:
            raise ValueError("required scenario must select at least one case")
        if len(self.case_ids) != len(set(self.case_ids)):
            raise ValueError("resolved scenario case IDs contain a duplicate")
        return self


class ScenarioPlan(ClosedModel):
    schema_id: Literal["kamiwaza.scenario-plan/v1"] = Field(alias="schema")
    profile_digest: Digest
    provider_revision: NonEmptyText
    selected: tuple[ResolvedScenario, ...]
    install_requirements: dict[str, JsonValue]
    runtime_requirements: tuple[StableId, ...]

    @model_validator(mode="after")
    def validate_selected_scenarios(self) -> ScenarioPlan:
        keys = [(item.target_id, item.scenario_id) for item in self.selected]
        if len(keys) != len(set(keys)):
            raise ValueError("scenario plan contains a duplicate target/scenario cell")
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
    results: tuple[CaseResult, ...]
    resolved_runtime: dict[str, JsonValue]


class RuntimeCluster(ClosedModel):
    id: StableId
    base_url: Annotated[str, Field(pattern=r"^https?://", max_length=2048)]
    api_key_ref: Annotated[
        str, Field(pattern=r"^(secret|file)://", max_length=4096, repr=False)
    ]
    kubeconfig_ref: Annotated[
        str, Field(pattern=r"^file://", max_length=4096, repr=False)
    ]


class RuntimeContext(ClosedModel):
    schema_id: Literal["kamiwaza.runtime-context/v1"] = Field(alias="schema")
    run_id: StableId
    clusters: tuple[RuntimeCluster, ...]

    @model_validator(mode="after")
    def validate_clusters(self) -> RuntimeContext:
        cluster_ids = [cluster.id for cluster in self.clusters]
        if not cluster_ids:
            raise ValueError("runtime context must declare at least one cluster")
        if len(cluster_ids) != len(set(cluster_ids)):
            raise ValueError("runtime cluster IDs contain a duplicate")
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

    @model_validator(mode="after")
    def validate_status(self) -> CleanupEvidence:
        if self.status == "passed":
            if any(result.status == "failed" for result in self.results):
                raise ValueError("passed cleanup contains a failure")
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
    schema_id: Literal["kamiwaza.coverage-summary/v1"] = Field(
        default="kamiwaza.coverage-summary/v1", alias="schema"
    )
    status: Literal["passed", "failed"]
    plan_digest: Digest
    issues: tuple[CoverageIssue, ...]

    @model_validator(mode="after")
    def validate_status(self) -> CoverageSummary:
        if self.status == "passed":
            if self.issues:
                raise ValueError("passed coverage contains issues")
        elif not self.issues:
            raise ValueError("failed coverage has no issues")
        return self
