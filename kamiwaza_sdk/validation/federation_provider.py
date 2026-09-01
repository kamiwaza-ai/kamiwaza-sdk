"""SDK-owned shared-IdP federation scenario provider.

The provider coordinates the lifecycle; setup, execution cases, and cleanup
are deliberately separate modules so each boundary remains reviewable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from kamiwaza_sdk.validation.applicability import (
    applicable_targets,
    descriptor_is_active,
)
from kamiwaza_sdk.validation.federation_cases import (
    CaseHooks as _CaseHooks,
    RetrievalRequest as _RetrievalRequest,
    RunContext as _RunContext,
    TenantDenialRequest as _TenantDenialRequest,
    _assert_tenant_denial as _assert_tenant_denial_impl,
    _mesh_retrieve as _mesh_retrieve_impl,
    run_edge as _run_edge_cases,
)
from kamiwaza_sdk.validation.federation_cleanup import (
    CleanupContext as _CleanupContext,
    cleanup_failure as _cleanup_failure,
    cleanup_mutation as _cleanup_mutation_impl,
)
from kamiwaza_sdk.validation.federation_common import (
    edge_cluster_ids as _edge_cluster_ids,
    edge_receiver_id as _edge_receiver_id,
    edge_state as _edge_state,
    jwt_subject,
    read_persona_password as _read_persona_password,
    resource_map as _resource_map,
    selected_endpoints as _selected_endpoints,
    token_client as _token_client,
)
from kamiwaza_sdk.validation.federation_runtime import (
    AdminFactory,
    ClusterFactory,
    KeycloakExternalTokenFactory,
    KeycloakAdminFactory,
    SdkFederationClusterFactory,
)
from kamiwaza_sdk.validation.federation_setup import (
    EdgeContext as _EdgeContext,
    RealmContext as _RealmContext,
    prepare_edge,
    prepare_realm,
)
from kamiwaza_sdk.validation.federation_spec import (
    FEDERATION_PROVIDER_REVISION,
    install_requirements,
    resolve_candidates,
    scenario_descriptor,
)
from kamiwaza_sdk.validation.federation_state import (
    FederationStateStore,
    validate_state,
)
from kamiwaza_sdk.validation.inference_state import runtime_ownership_key
from kamiwaza_sdk.validation.models import (
    CaseResult,
    CleanupEvidence,
    CleanupResult,
    FixtureState,
    ResolvedScenario,
    RuntimeCluster,
    RuntimeContext,
    ScenarioDescriptor,
    ScenarioEvidence,
    ScenarioPlan,
    ValidationProfile,
)
from kamiwaza_sdk.validation.provider import (
    FixtureStateWriter,
    ProviderContractError,
    validate_plan_runtime_identity,
    validate_state_runtime_identity,
)
from kamiwaza_sdk.validation.registry import model_digest


@dataclass
class _TeardownContext:
    runtime: RuntimeContext
    state: FixtureState
    runtime_clusters: Mapping[str, RuntimeCluster]
    clusters: Mapping[str, Any]
    admins: dict[str, Any]


class FederationLifecycleProvider:
    """Run the strict owned shared-IdP inventory for every selected edge."""

    def __init__(
        self,
        cluster_factory: ClusterFactory | None = None,
        admin_factory: AdminFactory | None = None,
        provider_revision: str = FEDERATION_PROVIDER_REVISION,
    ) -> None:
        self._cluster_factory = cluster_factory or SdkFederationClusterFactory()
        self._custom_admin_factory = admin_factory is not None
        self._admin_factory = admin_factory or KeycloakAdminFactory()
        self._provider_revision = provider_revision

    def describe(self) -> tuple[ScenarioDescriptor, ...]:
        return (scenario_descriptor(),)

    def resolve(self, profile: ValidationProfile) -> ScenarioPlan:
        descriptor = self.describe()[0]
        selected: tuple[ResolvedScenario, ...] = ()
        requirements: dict[str, Any] = {}
        if descriptor_is_active(profile, descriptor):
            if profile.validation.fixture_mode not in descriptor.fixture_modes:
                raise ProviderContractError(
                    "shared-IdP scenario does not support fixture mode"
                )
            candidates = applicable_targets(profile, descriptor)
            selected = resolve_candidates(
                profile,
                candidates,
                explicit=descriptor.scenario_id in profile.validation.include,
            )
            requirements = install_requirements(selected)
        return ScenarioPlan(
            schema="kamiwaza.scenario-plan/v1",
            profile_digest=model_digest(profile),
            provider_revision=self._provider_revision,
            selected=selected,
            install_requirements=requirements,
            runtime_requirements=descriptor.requires,
        )

    def prepare(
        self,
        plan: ScenarioPlan,
        runtime: RuntimeContext,
        state_writer: FixtureStateWriter,
    ) -> FixtureState:
        self._validate_revision(plan.provider_revision)
        validate_plan_runtime_identity(plan, runtime)
        store = FederationStateStore(
            state_writer, runtime_ownership_key(runtime), self._provider_revision
        )
        state = store.initial(plan, runtime)
        if not plan.selected:
            return state
        runtime_clusters = {item.id: item for item in runtime.clusters}
        clusters = self._open_clusters(runtime_clusters, plan)
        try:
            first = plan.selected[0]
            receiver_id = _selected_endpoints(first)[1]
            admin = self._admin_for(
                runtime,
                runtime_clusters[receiver_id],
                first.redacted_parameters,
            )
            state = prepare_realm(
                _RealmContext(state, store, plan, runtime, admin, first.target_id)
            )
            for selected in plan.selected:
                edge_params = {
                    **dict(selected.redacted_parameters),
                    **dict(_edge_state(state, selected.target_id)),
                }
                state = self._prepare_edge(
                    _EdgeContext(
                        state=state,
                        store=store,
                        selected=selected,
                        clusters=clusters,
                        runtime_clusters=runtime_clusters,
                        runtime=runtime,
                        admin=admin,
                        params=edge_params,
                    )
                )
            return state
        finally:
            for cluster in clusters.values():
                _close(cluster)

    def _prepare_edge(self, context: _EdgeContext) -> FixtureState:
        """Prepare one edge; subclasses may add owned resources before users."""

        return prepare_edge(context)

    def run(
        self,
        plan: ScenarioPlan,
        runtime: RuntimeContext,
        state: FixtureState,
    ) -> ScenarioEvidence:
        self._validate_revision(plan.provider_revision)
        validate_plan_runtime_identity(plan, runtime)
        validate_state(runtime, state, self._provider_revision)
        if not plan.selected:
            return ScenarioEvidence(
                schema="kamiwaza.scenario-evidence/v1",
                provider_revision=self._provider_revision,
                profile_digest=plan.profile_digest,
                plan_digest=model_digest(plan),
                state_digest=model_digest(state),
                results=(),
                resolved_runtime={},
            )
        runtime_clusters = {item.id: item for item in runtime.clusters}
        clusters = self._open_clusters(runtime_clusters, plan)
        results: list[CaseResult] = []
        resolved: dict[str, Any] = {}
        try:
            for selected in plan.selected:
                edge = _edge_state(state, selected.target_id)
                params = _resource_map(edge)
                initiator_id, receiver_id = _selected_endpoints(selected)
                admin = self._admin_for(
                    runtime,
                    runtime_clusters[receiver_id],
                    selected.redacted_parameters,
                )
                results.extend(
                    self._run_edge(
                        _RunContext(
                            selected=selected,
                            params=params,
                            initiator=clusters[initiator_id].client,
                            receiver=clusters[receiver_id].client,
                            admin=admin,
                            password=_read_persona_password(runtime),
                            initiator_base=runtime_clusters[initiator_id].base_url,
                        )
                    )
                )
                resolved[selected.target_id] = {
                    key: params.get(key)
                    for key in (
                        "issuer",
                        "federation_name",
                        "dataset_urn",
                        "initiator_cluster_id",
                        "receiver_cluster_id",
                    )
                }
        finally:
            for cluster in clusters.values():
                _close(cluster)
        return ScenarioEvidence(
            schema="kamiwaza.scenario-evidence/v1",
            provider_revision=self._provider_revision,
            profile_digest=plan.profile_digest,
            plan_digest=model_digest(plan),
            state_digest=model_digest(state),
            results=tuple(results),
            resolved_runtime=resolved,
        )

    def teardown(self, runtime: RuntimeContext, state: FixtureState) -> CleanupEvidence:
        validate_state_runtime_identity(runtime, state)
        validate_state(runtime, state, self._provider_revision)
        if not state.journal:
            return CleanupEvidence(
                schema="kamiwaza.cleanup-evidence/v1",
                provider_revision=self._provider_revision,
                run_id=runtime.run_id,
                state_digest=model_digest(state),
                status="passed",
                results=(),
            )
        runtime_clusters = {item.id: item for item in runtime.clusters}
        clusters = self._open_cleanup_clusters(runtime_clusters, state)
        context = _TeardownContext(runtime, state, runtime_clusters, clusters, {})
        try:
            results = [
                self._cleanup_one(context, mutation)
                for mutation in reversed(state.journal)
            ]
        finally:
            for cluster in clusters.values():
                _close(cluster)
        failed = any(item.status == "failed" for item in results)
        return CleanupEvidence(
            schema="kamiwaza.cleanup-evidence/v1",
            provider_revision=self._provider_revision,
            run_id=runtime.run_id,
            state_digest=model_digest(state),
            status="failed" if failed else "passed",
            results=tuple(results),
        )

    def _run_edge(self, context: _RunContext) -> list[CaseResult]:
        return _run_edge_cases(
            context,
            hooks=_CaseHooks(_mesh_retrieve, _assert_tenant_denial, _token_client),
        )

    def _cleanup_one(self, context: _TeardownContext, mutation: Any) -> CleanupResult:
        try:
            receiver_id = _edge_receiver_id(
                _edge_state(context.state, mutation.target_id)
            )
            receiver_cluster = context.runtime_clusters.get(receiver_id)
            client_wrapper = context.clusters.get(receiver_id)
            if receiver_cluster is None or client_wrapper is None:
                raise RuntimeError("cleanup runtime omits receiver cluster")
            edge = _edge_state(context.state, mutation.target_id)
            edge_clusters = _edge_cluster_ids(edge)
            if not edge_clusters:
                raise RuntimeError("cleanup runtime omits initiator cluster")
            initiator_wrapper = context.clusters.get(edge_clusters[0])
            if initiator_wrapper is None:
                raise RuntimeError("cleanup runtime omits initiator cluster")
            admin = None
            if mutation.resource_type.startswith("keycloak-"):
                admin = context.admins.setdefault(
                    receiver_id,
                    self._admin_for(context.runtime, receiver_cluster, edge),
                )
            return _cleanup_mutation_impl(
                mutation,
                _CleanupContext(
                    resources=_resource_map(
                        _edge_state(context.state, mutation.target_id)
                    ),
                    receiver=client_wrapper.client,
                    admin=admin,
                    runtime=context.runtime,
                    initiator=initiator_wrapper.client,
                    provider_revision=self._provider_revision,
                ),
            )
        except Exception as exc:
            return _cleanup_failure(mutation, exc)

    def _admin_for(
        self,
        runtime: RuntimeContext,
        runtime_cluster: RuntimeCluster,
        params: Mapping[str, Any],
    ) -> Any:
        if params.get("fixture_mode") == "external" and not self._custom_admin_factory:
            issuer = params.get("issuer")
            if not isinstance(issuer, str) or not issuer:
                raise ProviderContractError("external shared-IdP issuer is missing")
            return KeycloakExternalTokenFactory(issuer)(runtime, runtime_cluster)
        return self._admin_factory(runtime, runtime_cluster)

    def _open_clusters(
        self,
        runtime_clusters: Mapping[str, RuntimeCluster],
        plan: ScenarioPlan,
    ) -> dict[str, Any]:
        ids = {
            cluster_id
            for selected in plan.selected
            for cluster_id in _selected_endpoints(selected)
        }
        return {
            cluster_id: self._cluster_factory(runtime_clusters[cluster_id])
            for cluster_id in ids
        }

    def _open_cleanup_clusters(
        self, runtime_clusters: Mapping[str, RuntimeCluster], state: FixtureState
    ) -> dict[str, Any]:
        edges = state.opaque.get("edges")
        edge_values = edges.values() if isinstance(edges, Mapping) else ()
        ids = {
            str(cluster_id)
            for item in edge_values
            if isinstance(item, Mapping)
            for cluster_id in _edge_cluster_ids(item)
            if cluster_id
        }
        return {
            cluster_id: self._cluster_factory(runtime_clusters[cluster_id])
            for cluster_id in ids
        }

    def _validate_revision(self, revision: str) -> None:
        if revision != self._provider_revision:
            raise ProviderContractError("provider revision mismatch")


def _mesh_retrieve(request: _RetrievalRequest) -> Any:
    return _mesh_retrieve_impl(request)


def _jwt_subject(token: str) -> str:
    return jwt_subject(token)


def _assert_tenant_denial(request: _TenantDenialRequest) -> None:
    _assert_tenant_denial_impl(request)


def main(argv: Sequence[str] | None = None) -> int:
    from kamiwaza_sdk.validation.cli import provider_main

    return provider_main(FederationLifecycleProvider(), argv)


def _close(resource: Any) -> None:
    close = getattr(resource, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
