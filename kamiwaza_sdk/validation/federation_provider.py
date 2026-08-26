"""SDK-owned shared-IdP federation scenario provider.

The provider owns product semantics for the M3 shared-IdP edge.  Kajiya only
passes facts and runtime references; it does not know how to create realms,
pair clusters, seed ReBAC, or decide the exact retrieval/job assertions.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import shlex
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast
from urllib.parse import quote

import requests  # type: ignore[import-untyped]

from kamiwaza_sdk.services.federation_credentials import federation_credential_headers
from kamiwaza_sdk.validation.applicability import (
    applicable_targets,
    descriptor_is_active,
)
from kamiwaza_sdk.validation.federation_fixture import (
    DEFAULT_TENANT_ID,
    GATE_CLASSPATH,
    GATE_NAME,
    GATE_PACKAGE_NAME,
    GATE_PACKAGE_SPEC,
    KNOWN,
    PERSONAS,
    TENANT_NEGATIVE_PERSONAS,
    UNONBOARDED_PERSONA,
    records,
)
from kamiwaza_sdk.validation.federation_runtime import (
    AdminFactory,
    ClusterFactory,
    KeycloakAdminFactory,
    SdkFederationClusterFactory,
    read_file_reference,
)
from kamiwaza_sdk.validation.federation_spec import (
    FEDERATION_PROVIDER_REVISION,
    SHARED_REALM_CLIENT_ID,
    SHARED_REALM_PERSONA_PASSWORD_REF,
    install_requirements,
    resolve_candidates,
    scenario_descriptor,
)
from kamiwaza_sdk.validation.federation_state import (
    FederationStateStore,
    owner_nonce,
    validate_state,
)
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
from kamiwaza_sdk.validation.inference_state import runtime_ownership_key

_ALLOW_ALL_EXECUTION_GATE = (
    "kamiwaza.services.authz.gates.default_gates.AllowAllExecutionGate"
)
_GATE_INDEX_ENV = "KAMIWAZA_FEDERATION_GATE_INDEX_URL"
_GATE_HASH_ENV = "KAMIWAZA_FEDERATION_GATE_HASH"
_GATE_SPEC_ENV = "KAMIWAZA_FEDERATION_GATE_PACKAGE_SPEC"
_DATASET_PATH_ENV = "KAMIWAZA_FEDERATION_DATASET_PATH"
_DATASET_DEFAULT_PATH = "/app/models/kamiwaza-validation-mini-clearance.csv"


@dataclass
class _RealmContext:
    state: FixtureState
    store: FederationStateStore
    plan: ScenarioPlan
    runtime: RuntimeContext
    admin: Any
    target_id: str


@dataclass
class _EdgeContext:
    state: FixtureState
    store: FederationStateStore
    selected: ResolvedScenario
    clusters: Mapping[str, Any]
    runtime_clusters: Mapping[str, RuntimeCluster]
    runtime: RuntimeContext
    admin: Any
    params: dict[str, Any]
    initiator_id: str = ""
    receiver_id: str = ""
    initiator: Any = None
    receiver: Any = None
    realm: str = ""
    issuer: str = ""
    name: str = ""
    urn: str = ""
    source_cluster_id: str = ""
    resolved_receiver_id: str = ""


@dataclass(frozen=True)
class _RunContext:
    selected: ResolvedScenario
    params: Mapping[str, Any]
    initiator: Any
    receiver: Any
    admin: Any
    password: str
    initiator_base: str


@dataclass(frozen=True)
class _CleanupContext:
    resources: Mapping[str, Any]
    receiver: Any
    admin: Any
    runtime: RuntimeContext


@dataclass
class _TeardownContext:
    runtime: RuntimeContext
    state: FixtureState
    runtime_clusters: Mapping[str, RuntimeCluster]
    clusters: Mapping[str, Any]
    admins: dict[str, Any]


@dataclass(frozen=True)
class _RetrievalRequest:
    persona: Any
    base_url: str
    token: str
    federation_name: str
    dataset_urn: str
    job_id: Any = None
    credential_headers: Mapping[str, str] | None = None


@dataclass(frozen=True)
class _TenantDenialRequest:
    initiator: Any
    base_url: str
    token: str
    federation_name: str
    dataset_urn: str
    expected_status: int


class FederationLifecycleProvider:
    """Run the strict owned shared-IdP inventory for every selected edge."""

    def __init__(
        self,
        cluster_factory: ClusterFactory | None = None,
        admin_factory: AdminFactory | None = None,
    ) -> None:
        self._cluster_factory = cluster_factory or SdkFederationClusterFactory()
        self._admin_factory = admin_factory or KeycloakAdminFactory()

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
            provider_revision=FEDERATION_PROVIDER_REVISION,
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
        key = runtime_ownership_key(runtime)
        store = FederationStateStore(state_writer, key, FEDERATION_PROVIDER_REVISION)
        state = store.initial(plan, runtime)
        if not plan.selected:
            return state

        runtime_clusters = {item.id: item for item in runtime.clusters}
        clusters = self._open_clusters(runtime_clusters, plan)
        try:
            first = plan.selected[0]
            first_receiver = runtime_clusters[
                (first.cluster_ids or (first.cluster_id,))[1]
            ]
            admin = self._admin_factory(runtime, first_receiver)
            state = self._prepare_realm(
                _RealmContext(state, store, plan, runtime, admin, first.target_id)
            )
            for selected in plan.selected:
                state = self._prepare_edge(
                    _EdgeContext(
                        state=state,
                        store=store,
                        selected=selected,
                        clusters=clusters,
                        runtime_clusters=runtime_clusters,
                        runtime=runtime,
                        admin=admin,
                        params=dict(selected.redacted_parameters),
                    )
                )
            return state
        finally:
            for cluster in clusters.values():
                _close(cluster)

    def run(
        self,
        plan: ScenarioPlan,
        runtime: RuntimeContext,
        state: FixtureState,
    ) -> ScenarioEvidence:
        self._validate_revision(plan.provider_revision)
        validate_plan_runtime_identity(plan, runtime)
        validate_state(runtime, state, FEDERATION_PROVIDER_REVISION)
        if not plan.selected:
            return ScenarioEvidence(
                schema="kamiwaza.scenario-evidence/v1",
                provider_revision=FEDERATION_PROVIDER_REVISION,
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
                admin = self._admin_factory(runtime, runtime_clusters[receiver_id])
                edge_results = self._run_edge(
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
                results.extend(edge_results)
                resolved[selected.target_id] = {
                    "issuer": params.get("issuer"),
                    "federation_name": params.get("federation_name"),
                    "dataset_urn": params.get("dataset_urn"),
                    "initiator_cluster_id": params.get("initiator_cluster_id"),
                    "receiver_cluster_id": params.get("receiver_cluster_id"),
                }
        finally:
            for cluster in clusters.values():
                _close(cluster)
        return ScenarioEvidence(
            schema="kamiwaza.scenario-evidence/v1",
            provider_revision=FEDERATION_PROVIDER_REVISION,
            profile_digest=plan.profile_digest,
            plan_digest=model_digest(plan),
            state_digest=model_digest(state),
            results=tuple(results),
            resolved_runtime=resolved,
        )

    def teardown(self, runtime: RuntimeContext, state: FixtureState) -> CleanupEvidence:
        validate_state_runtime_identity(runtime, state)
        validate_state(runtime, state, FEDERATION_PROVIDER_REVISION)
        if not state.journal:
            return CleanupEvidence(
                schema="kamiwaza.cleanup-evidence/v1",
                provider_revision=FEDERATION_PROVIDER_REVISION,
                run_id=runtime.run_id,
                state_digest=model_digest(state),
                status="passed",
                results=(),
            )
        runtime_clusters = {item.id: item for item in runtime.clusters}
        clusters = self._open_cleanup_clusters(runtime_clusters, state)
        context = _TeardownContext(
            runtime=runtime,
            state=state,
            runtime_clusters=runtime_clusters,
            clusters=clusters,
            admins={},
        )
        try:
            results = self._cleanup_journal(context)
        finally:
            for cluster in clusters.values():
                _close(cluster)
        failed = any(item.status == "failed" for item in results)
        return CleanupEvidence(
            schema="kamiwaza.cleanup-evidence/v1",
            provider_revision=FEDERATION_PROVIDER_REVISION,
            run_id=runtime.run_id,
            state_digest=model_digest(state),
            status="failed" if failed else "passed",
            results=tuple(results),
        )

    def _cleanup_journal(self, context: _TeardownContext) -> list[CleanupResult]:
        return [
            self._cleanup_one(context, mutation)
            for mutation in reversed(context.state.journal)
        ]

    def _cleanup_one(self, context: _TeardownContext, mutation: Any) -> CleanupResult:
        try:
            receiver_id = _edge_receiver_id(
                _edge_state(context.state, mutation.target_id)
            )
            receiver_cluster = context.runtime_clusters.get(receiver_id)
            client_wrapper = context.clusters.get(receiver_id)
            if receiver_cluster is None or client_wrapper is None:
                raise RuntimeError("cleanup runtime omits receiver cluster")
            admin = context.admins.setdefault(
                receiver_id, self._admin_factory(context.runtime, receiver_cluster)
            )
            return self._cleanup_mutation(
                mutation,
                _CleanupContext(
                    resources=_resource_map(
                        _edge_state(context.state, mutation.target_id)
                    ),
                    receiver=client_wrapper.client,
                    admin=admin,
                    runtime=context.runtime,
                ),
            )
        except Exception as exc:
            return _cleanup_failure(mutation, exc)

    def _prepare_realm(self, context: _RealmContext) -> FixtureState:
        selected = context.plan.selected[0]
        params = selected.redacted_parameters
        realm = _required_text(params, "realm")
        nonce = owner_nonce(runtime_ownership_key(context.runtime), realm)
        state = context.store.record(
            context.state,
            target_id=context.target_id,
            resource_type="keycloak-realm",
            resource_id=realm,
            opaque={"realm": realm, "issuer": _required_text(params, "issuer")},
        )
        # Record the realm before child mutations.  If a later step fails,
        # teardown can still prove and remove the owned namespace.
        created = context.admin.create_owned_realm(realm, nonce)
        if not created:
            raise ProviderContractError("shared-IdP realm creation returned no result")
        context.admin.set_unmanaged_attributes(realm)
        client = context.admin.ensure_ropc_client(realm, SHARED_REALM_CLIENT_ID)
        client_uuid = _required_text(client, "id")
        state = context.store.record(
            state,
            target_id=context.target_id,
            resource_type="keycloak-client",
            resource_id=client_uuid,
            opaque={"client_uuid": client_uuid},
        )
        for attribute in ("clearance", "tenant_id", "tenant"):
            context.admin.ensure_attribute_mapper(
                realm, client_uuid, attribute=attribute
            )
        password = _read_persona_password(context.runtime)
        for username, attributes in _all_personas():
            user = context.admin.ensure_user(
                realm,
                username,
                password=password,
                attributes=attributes,
            )
            user_id = _required_text(user, "id")
            state = context.store.record(
                state,
                target_id=context.target_id,
                resource_type="keycloak-user",
                resource_id=user_id,
                opaque={f"user:{username}": user_id},
            )
        return context.store.update_edge(
            state,
            context.target_id,
            {
                "realm": realm,
                "issuer": _required_text(params, "issuer"),
                "client_id": SHARED_REALM_CLIENT_ID,
                "client_uuid": client_uuid,
                "owner_nonce_derived": True,
            },
        )

    def _prepare_edge(self, context: _EdgeContext) -> FixtureState:
        self._bind_edge(context)
        self._configure_edge(context)
        self._seed_brokered_users(context)
        return context.state

    def _bind_edge(self, context: _EdgeContext) -> None:
        context.initiator_id, context.receiver_id = _selected_endpoints(
            context.selected
        )
        context.initiator = context.clusters[context.initiator_id].client
        context.receiver = context.clusters[context.receiver_id].client
        context.realm = _required_text(context.params, "realm")
        context.issuer = _required_text(context.params, "issuer")
        context.name = _federation_name(
            context.runtime.run_id, context.selected.target_id
        )
        psk = secrets.token_urlsafe(32)
        shared = {
            "shared_issuer_url": context.issuer,
            "shared_jwks_url": context.issuer.rstrip("/")
            + "/protocol/openid-connect/certs",
        }
        receiver_record = context.receiver.federations.pair(
            name=context.name, role="receiver", preshared_key=psk, **shared
        )
        receiver_id = _required_text(receiver_record, "id")
        context.state = context.store.record(
            context.state,
            target_id=context.selected.target_id,
            resource_type="receiver-federation",
            resource_id=receiver_id,
            opaque={"receiver_federation_id": receiver_id},
        )
        initiator_record = context.initiator.federations.pair(
            name=context.name,
            role="initiator",
            remote_url=context.runtime_clusters[context.receiver_id].base_url,
            preshared_key=psk,
            **shared,
        )
        initiator_id = _required_text(initiator_record, "id")
        context.state = context.store.record(
            context.state,
            target_id=context.selected.target_id,
            resource_type="initiator-federation",
            resource_id=initiator_id,
            opaque={"initiator_federation_id": initiator_id},
        )
        context.source_cluster_id = (
            _optional_text(
                context.receiver.federations.get(receiver_id), "remote_cluster_id"
            )
            or context.initiator_id
        )
        context.resolved_receiver_id = (
            _optional_text(
                context.initiator.federations.get(initiator_id), "remote_cluster_id"
            )
            or context.receiver_id
        )
        if context.source_cluster_id == context.resolved_receiver_id:
            raise ProviderContractError(
                "shared-IdP pairing resolved one cluster identity"
            )

    def _configure_edge(self, context: _EdgeContext) -> None:
        for client in (context.initiator, context.receiver):
            client.cluster.declare_attribute("clearance", type="string")
        if self._ensure_gate(context.receiver):
            context.state = context.store.record(
                context.state,
                target_id=context.selected.target_id,
                resource_type="gate-package",
                resource_id=GATE_PACKAGE_NAME,
            )
        dataset_path = os.environ.get(_DATASET_PATH_ENV, _DATASET_DEFAULT_PATH).strip()
        if not dataset_path:
            raise ProviderContractError("shared-IdP dataset path is empty")
        context.urn = str(
            context.receiver.datasets.create(
                name=f"kamiwaza-validation-{context.name}",
                platform="file",
                properties={"path": dataset_path},
            )
        )
        context.state = context.store.record(
            context.state,
            target_id=context.selected.target_id,
            resource_type="dataset",
            resource_id=context.urn,
            opaque={"dataset_urn": context.urn},
        )
        context.receiver.datasets.set_gate(context.urn, type=GATE_CLASSPATH, config={})
        previous_gate = _read_execution_gate(context.receiver)
        context.receiver.cluster.set_execution_gate(
            type=_ALLOW_ALL_EXECUTION_GATE, config={}
        )
        context.state = context.store.record(
            context.state,
            target_id=context.selected.target_id,
            resource_type="execution-gate",
            resource_id=context.receiver_id,
            opaque={"previous_execution_gate": _json_value(previous_gate)},
        )
        context.state = context.store.update_edge(
            context.state,
            context.selected.target_id,
            {
                "federation_name": context.name,
                "realm": context.realm,
                "issuer": context.issuer,
                "dataset_urn": context.urn,
                "initiator_cluster_id": context.source_cluster_id,
                "receiver_cluster_id": context.resolved_receiver_id,
                "source_profile_cluster_id": context.initiator_id,
                "receiver_profile_cluster_id": context.receiver_id,
            },
        )

    def _seed_brokered_users(self, context: _EdgeContext) -> None:
        password_ref = context.runtime.secret_refs.get(
            SHARED_REALM_PERSONA_PASSWORD_REF
        )
        if not password_ref:
            raise ProviderContractError(
                "shared-IdP persona password reference is missing"
            )
        password = read_file_reference(
            password_ref, label="shared-IdP persona password"
        )
        for clearance, username in PERSONAS.items():
            context.state = self._seed_brokered_user(
                context,
                username,
                password,
                _initial_tuples(context.urn, job_executor=clearance == "U"),
            )
        for case_name, (username, _attrs) in TENANT_NEGATIVE_PERSONAS.items():
            context.state = self._seed_brokered_user(
                context, username, password, [], label=case_name
            )

    def _seed_brokered_user(
        self,
        context: _EdgeContext,
        username: str,
        password: str,
        tuples: list[dict[str, str]],
        *,
        label: str | None = None,
    ) -> FixtureState:
        token = context.admin.ropc_token(
            context.realm, SHARED_REALM_CLIENT_ID, username, password
        )
        external_id = f"{_jwt_subject(token)}@{context.source_cluster_id}"
        context.receiver.federations[context.name].users.add(
            external_id, initial_tuples=tuples
        )
        resource_label = label or username
        return context.store.record(
            context.state,
            target_id=context.selected.target_id,
            resource_type="brokered-user",
            resource_id=external_id,
            opaque={f"brokered:{resource_label}": external_id},
        )

    def _ensure_gate(self, receiver: Any) -> bool:
        packages = receiver.gates.packages.list()
        installed = _find_gate_package(packages)
        if installed is None:
            _install_gate_package(receiver)
        _validate_gate_discovery(receiver)
        return installed is None

    def _run_edge(self, context: _RunContext) -> list[CaseResult]:
        results: list[CaseResult] = []
        for case_id in context.selected.case_ids:
            results.append(_run_one_case(context, case_id))
        return results

    def _cleanup_mutation(
        self, mutation: Any, context: _CleanupContext
    ) -> CleanupResult:
        handler = _CLEANUP_HANDLERS.get(mutation.resource_type)
        if handler is None:
            raise RuntimeError("unsupported fixture resource")
        handler(mutation, context)
        return CleanupResult(
            target_id=mutation.target_id,
            resource_type=mutation.resource_type,
            resource_id=mutation.resource_id,
            status="removed",
            detail=None,
        )

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

    @staticmethod
    def _validate_revision(revision: str) -> None:
        if revision != FEDERATION_PROVIDER_REVISION:
            raise ProviderContractError("provider revision mismatch")


def main(argv: Sequence[str] | None = None) -> int:
    from kamiwaza_sdk.validation.cli import provider_main

    return provider_main(FederationLifecycleProvider(), argv)


def _cleanup_brokered_user(mutation: Any, context: _CleanupContext) -> None:
    name = _optional_text(context.resources, "federation_name")
    federation_id = _optional_text(context.resources, "receiver_federation_id")
    if not name or not federation_id:
        raise RuntimeError("brokered-user cleanup locator is incomplete")
    external = _brokered_external_id(mutation, context.resources)
    context.receiver._request(
        "POST",
        f"/cluster/federations/{quote(federation_id, safe='')}/users/"
        f"{quote(external, safe='')}/revoke",
        params={"cancel_in_flight_jobs": "true"},
    )


def _brokered_external_id(mutation: Any, resources: Mapping[str, Any]) -> str:
    for key, value in resources.items():
        if key.startswith("brokered:") and value == mutation.resource_id:
            return str(value)
    return mutation.resource_id


def _cleanup_dataset(mutation: Any, context: _CleanupContext) -> None:
    context.receiver.datasets.delete(mutation.resource_id)


def _cleanup_federation(mutation: Any, context: _CleanupContext) -> None:
    context.receiver._request(
        "DELETE", f"/cluster/federations/{quote(mutation.resource_id, safe='')}"
    )


def _cleanup_execution_gate(mutation: Any, context: _CleanupContext) -> None:
    del mutation
    previous = context.resources.get("previous_execution_gate")
    if previous is None:
        context.receiver.cluster.clear_execution_gate()
        return
    if not isinstance(previous, Mapping):
        raise RuntimeError("execution-gate cleanup snapshot is invalid")
    context.receiver.cluster.set_execution_gate(
        type=_required_text(previous, "type"),
        config=previous.get("config") or {},
    )


def _cleanup_gate_package(mutation: Any, context: _CleanupContext) -> None:
    context.receiver.gates.packages.uninstall(mutation.resource_id)


def _cleanup_keycloak_user(mutation: Any, context: _CleanupContext) -> None:
    context.admin.delete_user(_cleanup_realm(context), mutation.resource_id)


def _cleanup_keycloak_client(mutation: Any, context: _CleanupContext) -> None:
    context.admin.delete_client(_cleanup_realm(context), mutation.resource_id)


def _cleanup_keycloak_realm(mutation: Any, context: _CleanupContext) -> None:
    realm = _cleanup_realm(context)
    nonce = owner_nonce(runtime_ownership_key(context.runtime), realm)
    context.admin.delete_owned_realm(realm, nonce)


def _cleanup_realm(context: _CleanupContext) -> str:
    realm = _optional_text(context.resources, "realm")
    if not realm:
        raise RuntimeError("keycloak cleanup realm is missing")
    return realm


_CleanupHandler = Callable[[Any, _CleanupContext], None]
_CLEANUP_HANDLERS: dict[str, _CleanupHandler] = {
    "brokered-user": _cleanup_brokered_user,
    "dataset": _cleanup_dataset,
    "receiver-federation": _cleanup_federation,
    "initiator-federation": _cleanup_federation,
    "execution-gate": _cleanup_execution_gate,
    "gate-package": _cleanup_gate_package,
    "keycloak-user": _cleanup_keycloak_user,
    "keycloak-client": _cleanup_keycloak_client,
    "keycloak-realm": _cleanup_keycloak_realm,
}


def _run_one_case(context: _RunContext, case_id: str) -> CaseResult:
    started = time.monotonic()
    try:
        _dispatch_case(context, case_id)
    except Exception as exc:
        return CaseResult(
            target_id=context.selected.target_id,
            scenario_id=context.selected.scenario_id,
            case_id=case_id,
            status="failed",
            duration_ms=_elapsed_ms(started),
            detail=f"{type(exc).__name__}: validation assertion failed",
        )
    return CaseResult(
        target_id=context.selected.target_id,
        scenario_id=context.selected.scenario_id,
        case_id=case_id,
        status="passed",
        duration_ms=_elapsed_ms(started),
        detail=None,
    )


def _dispatch_case(context: _RunContext, case_id: str) -> None:
    if case_id.startswith("retrieval-clearance-"):
        _run_clearance_case(context, case_id.rsplit("-", 1)[-1].upper())
        return
    if case_id.startswith("retrieval-invalid-tenant-"):
        _run_tenant_case(context, case_id.removeprefix("retrieval-invalid-tenant-"))
        return
    handler = {
        "dataset-list-authorized-fixture": _run_dataset_case,
        "job-reaches-receiver-marker": _run_job_case,
        "unonboarded-user-rejected": _run_unonboarded_case,
    }.get(case_id)
    if handler is None:
        raise ProviderContractError("provider case is not registered")
    handler(context)


def _run_clearance_case(context: _RunContext, clearance: str) -> None:
    username = PERSONAS[clearance]
    token = _issue_token(context, username)
    persona = _token_client(context.initiator_base, token)
    try:
        rows, audits = _mesh_retrieve(
            _RetrievalRequest(
                persona=persona,
                base_url=context.initiator_base,
                token=token,
                federation_name=_required_text(context.params, "federation_name"),
                dataset_urn=_required_text(context.params, "dataset_urn"),
            )
        )
    finally:
        _close_client(persona)
    included, allowed = KNOWN[clearance]
    expected = [row for row in records() if row["classification"] in allowed]
    if len(rows) != included or _sort_rows(rows) != _sort_rows(expected):
        raise AssertionError("gated retrieval returned unexpected rows")
    if not audits:
        raise AssertionError("gated retrieval emitted no gate audit")


def _run_tenant_case(context: _RunContext, case_name: str) -> None:
    username, _attrs = TENANT_NEGATIVE_PERSONAS[case_name]
    token = _issue_token(context, username)
    expected_status = 403 if case_name == "canonical-nondefault" else 401
    _assert_tenant_denial(
        _TenantDenialRequest(
            initiator=context.initiator,
            base_url=context.initiator_base,
            token=token,
            federation_name=_required_text(context.params, "federation_name"),
            dataset_urn=_required_text(context.params, "dataset_urn"),
            expected_status=expected_status,
        )
    )


def _run_dataset_case(context: _RunContext) -> None:
    token = _issue_token(context, PERSONAS["U"])
    persona = _token_client(context.initiator_base, token)
    try:
        datasets = persona.catalog.datasets.list(
            target_cluster=_required_text(context.params, "federation_name")
        )
        urn = _required_text(context.params, "dataset_urn")
        if [str(item.urn) for item in datasets] != [urn]:
            raise AssertionError("mesh dataset listing was not authorization scoped")
    finally:
        _close_client(persona)


def _run_job_case(context: _RunContext) -> None:
    token = _issue_token(context, PERSONAS["U"])
    persona = _token_client(context.initiator_base, token)
    marker = f"kamiwaza-validation-{context.selected.target_id}"
    script = (
        "import json; print('KZ_MESH_RUN_ON_JSON::' + json.dumps({'probe': "
        + repr(marker)
        + "}))"
    )
    try:
        result = persona.jobs.run(
            entrypoint="python3 -c " + shlex.quote(script),
            target_cluster=_required_text(context.params, "federation_name"),
            timeout_seconds=120,
            recoverable=True,
        )
        if getattr(result, "status", None) != "SUCCEEDED":
            raise AssertionError("mesh job did not succeed")
        probe = getattr(result, "probe", None)
        if probe is None and isinstance(getattr(result, "result", None), dict):
            probe = result.result.get("probe")
        if probe != marker:
            raise AssertionError("mesh job marker did not round-trip")
    finally:
        _close_client(persona)


def _run_unonboarded_case(context: _RunContext) -> None:
    token = _issue_token(context, UNONBOARDED_PERSONA)
    persona = _token_client(context.initiator_base, token)
    name = _required_text(context.params, "federation_name")
    try:
        try:
            persona._request(
                "GET", f"/mesh/{quote(name, safe='')}/api/cluster/diagnose"
            )
        except Exception as exc:
            if getattr(exc, "status_code", None) != 403:
                raise
            if _error_reason(exc) != "unauthorized_brokered_user":
                raise AssertionError("unexpected unonboarded-user denial")
            return
        raise AssertionError("unonboarded shared-IDP user was admitted")
    finally:
        _close_client(persona)


def _issue_token(context: _RunContext, username: str) -> str:
    return context.admin.ropc_token(
        _required_text(context.params, "realm"),
        SHARED_REALM_CLIENT_ID,
        username,
        context.password,
    )


def _read_persona_password(runtime: RuntimeContext) -> str:
    reference = runtime.secret_refs.get(SHARED_REALM_PERSONA_PASSWORD_REF)
    if not reference:
        raise ProviderContractError("shared-IdP persona password reference is missing")
    return read_file_reference(reference, label="shared-IdP persona password")


def _sort_rows(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(rows, key=lambda row: str(row.get("id", "")))


def _all_personas() -> tuple[tuple[str, dict[str, str]], ...]:
    values = [
        (username, {"clearance": clearance, "tenant_id": DEFAULT_TENANT_ID})
        for clearance, username in PERSONAS.items()
    ]
    values.append(
        (UNONBOARDED_PERSONA, {"clearance": "U", "tenant_id": DEFAULT_TENANT_ID})
    )
    values.extend(TENANT_NEGATIVE_PERSONAS.values())
    return tuple(values)


def _initial_tuples(dataset_urn: str, *, job_executor: bool) -> list[dict[str, str]]:
    tuples = [
        {
            "subject": "user:{{user_id}}",
            "relation": "viewer",
            "object": f"dataset:{dataset_urn}",
        }
    ]
    if job_executor:
        tuples.append(
            {
                "subject": "user:{{user_id}}",
                "relation": "executor",
                "object": "cluster_jobs:__all__",
            }
        )
    return tuples


def _selected_endpoints(selected: ResolvedScenario) -> tuple[str, str]:
    values = selected.cluster_ids or (selected.cluster_id,)
    if len(values) != 2 or values[0] == values[1]:
        raise ProviderContractError(
            "shared-IdP selection must bind two distinct clusters"
        )
    return values[0], values[1]


def _receiver_id(selected: ResolvedScenario) -> str:
    return _selected_endpoints(selected)[1]


def _edge_receiver_id(edge: Mapping[str, Any]) -> str:
    values = _edge_cluster_ids(edge)
    if len(values) != 2:
        raise ProviderContractError("fixture state edge does not bind two clusters")
    return values[1]


def _edge_state(state: FixtureState, target_id: str) -> Mapping[str, Any]:
    edges = state.opaque.get("edges")
    if not isinstance(edges, Mapping) or not isinstance(edges.get(target_id), Mapping):
        raise ProviderContractError("fixture state is missing selected edge")
    return cast(Mapping[str, Any], edges[target_id])


def _resource_map(edge: Mapping[str, Any]) -> Mapping[str, Any]:
    resources = edge.get("resources")
    if not isinstance(resources, Mapping):
        raise ProviderContractError("fixture state edge resources are invalid")
    return {**dict(edge), **dict(resources)}


def _edge_cluster_ids(edge: Mapping[str, Any]) -> tuple[str, ...]:
    values = edge.get("cluster_ids")
    if isinstance(values, list):
        return tuple(value for value in values if isinstance(value, str))
    fallback = edge.get("cluster_id")
    return (fallback,) if isinstance(fallback, str) else ()


def _required_text(value: Any, key: str) -> str:
    if isinstance(value, Mapping):
        candidate = value.get(key)
    else:
        candidate = getattr(value, key, None)
    if not isinstance(candidate, str) or not candidate:
        raise ProviderContractError(f"shared-IdP value {key!r} is missing")
    return candidate


def _optional_text(value: Any, key: str) -> str | None:
    candidate = (
        value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)
    )
    if isinstance(candidate, str) and candidate:
        return candidate
    if candidate is None or isinstance(candidate, (Mapping, list, tuple, set)):
        return None
    rendered = str(candidate)
    return rendered or None


def _federation_name(run_id: str, target_id: str) -> str:
    import hashlib

    digest = hashlib.sha256(f"{run_id}:{target_id}".encode()).hexdigest()[:16]
    return f"kz-validation-{digest}"


def _jwt_subject(token: str) -> str:
    parts = token.split(".")
    if len(parts) != 3:
        raise ProviderContractError("shared-IdP token is not a JWT")
    encoded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ProviderContractError("shared-IdP token payload is invalid") from None
    subject = payload.get("sub") if isinstance(payload, Mapping) else None
    if not isinstance(subject, str) or not subject:
        raise ProviderContractError("shared-IdP token has no subject")
    return subject


def _token_client(base_url: str, token: str) -> Any:
    from kamiwaza_sdk import KamiwazaClient

    return KamiwazaClient(base_url=base_url, api_key=token)


def _close_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def _close(cluster: Any) -> None:
    close = getattr(cluster, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _read_execution_gate(client: Any) -> Any | None:
    try:
        return client.cluster.get_execution_gate().model_dump(mode="json")
    except Exception as exc:
        if getattr(exc, "status_code", None) == 404:
            return None
        raise


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (dict, list, str, int, float, bool)):
        return value
    return str(value)


def _find_gate_package(packages: Any) -> Any | None:
    items = getattr(packages, "items", packages) or []
    return next(
        (
            item
            for item in items
            if getattr(item, "name", None) == GATE_PACKAGE_NAME
            and GATE_CLASSPATH in (getattr(item, "classpaths", None) or [])
        ),
        None,
    )


def _install_gate_package(receiver: Any) -> None:
    digest = os.environ.get(_GATE_HASH_ENV, "").strip()
    index = os.environ.get(_GATE_INDEX_ENV, "").strip()
    if not digest or not index:
        raise ProviderContractError(
            "shared-IdP gate package is absent; configure gate index and hash"
        )
    spec = os.environ.get(_GATE_SPEC_ENV, GATE_PACKAGE_SPEC).strip()
    receiver.gates.packages.install(spec, hash_digest=digest, index_url=index)


def _validate_gate_discovery(receiver: Any) -> None:
    gate = receiver.gates.discover(GATE_CLASSPATH)
    if getattr(gate, "name", None) != GATE_NAME:
        raise ProviderContractError(
            "shared-IdP gate classpath resolved unexpected gate"
        )


def _mesh_retrieve(
    request: _RetrievalRequest,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    credential_headers = federation_credential_headers(request.federation_name)
    job = request.persona._request(
        "POST",
        f"/mesh/{quote(request.federation_name, safe='')}/api/retrieval/jobs",
        json={"dataset_urn": request.dataset_urn},
        **({"headers": credential_headers} if credential_headers else {}),
    )
    job_id = _retrieval_job_id(job)
    if not job_id:
        raise RuntimeError("mesh retrieval returned no job ID")
    response = _retrieval_stream(
        _RetrievalRequest(
            persona=request.persona,
            base_url=request.base_url,
            token=request.token,
            federation_name=request.federation_name,
            dataset_urn=request.dataset_urn,
            job_id=job_id,
            credential_headers=credential_headers,
        )
    )
    try:
        return _collect_retrieval_stream(response)
    finally:
        response.close()


def _retrieval_job_id(job: Any) -> Any:
    if isinstance(job, Mapping):
        return job.get("job_id") or job.get("id")
    return getattr(job, "job_id", None)


def _retrieval_stream(request: _RetrievalRequest) -> Any:
    headers = request.credential_headers or {}
    response = requests.get(
        f"{request.base_url}/mesh/{quote(request.federation_name, safe='')}/"
        f"api/retrieval/jobs/{quote(str(request.job_id), safe='')}/stream",
        headers={
            "Authorization": f"Bearer {request.token}",
            "Accept": "text/event-stream",
            **headers,
        },
        stream=True,
        verify=getattr(request.persona.session, "verify", True),
        timeout=120,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"mesh retrieval stream returned HTTP {response.status_code}"
        )
    return response


def _collect_retrieval_stream(
    response: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    event: str | None = None
    data_lines: list[str] = []
    for raw in response.iter_lines(decode_unicode=True):
        if raw:
            event, data_lines = _parse_sse_line(raw, event, data_lines)
        else:
            rows, audits = _consume_sse_chunk(event, data_lines, rows, audits)
            event, data_lines = None, []
    return rows, audits


def _parse_sse_line(
    raw: str, event: str | None, data_lines: list[str]
) -> tuple[str | None, list[str]]:
    if raw.startswith("event:"):
        return raw[6:].strip(), data_lines
    if raw.startswith("data:"):
        return event, [*data_lines, raw[5:].lstrip()]
    return event, data_lines


def _consume_sse_chunk(
    event: str | None,
    data_lines: Sequence[str],
    rows: list[dict[str, Any]],
    audits: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if event != "chunk" or not data_lines:
        return rows, audits
    payload = json.loads("\n".join(data_lines))
    rows.extend(_payload_rows(payload))
    audits.extend(_payload_audits(payload))
    return rows, audits


def _payload_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    rows = payload.get("data") or payload.get("records") or payload.get("rows") or []
    return (
        [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    )


def _payload_audits(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    metadata = payload.get("metadata")
    audit = metadata.get("gate_audit") if isinstance(metadata, Mapping) else None
    if isinstance(audit, list):
        return [item for item in audit if isinstance(item, dict)]
    return [audit] if isinstance(audit, dict) else []


def _assert_tenant_denial(
    request: _TenantDenialRequest,
) -> None:
    response = _tenant_denial_response(request)
    try:
        if response.status_code != request.expected_status:
            raise AssertionError("tenant-negative status did not match contract")
        try:
            payload = response.json()
        except ValueError:
            raise AssertionError("tenant-negative response was not JSON") from None
    finally:
        response.close()
    reason = payload.get("detail") if isinstance(payload, Mapping) else None
    expected_reason = (
        "mesh_tenant_not_admitted"
        if request.expected_status == 403
        else "tenant_required"
    )
    if reason != expected_reason:
        raise AssertionError("tenant-negative reason did not match contract")


def _tenant_denial_response(request: _TenantDenialRequest) -> Any:
    headers = {
        "Authorization": f"Bearer {request.token}",
        **federation_credential_headers(request.federation_name),
    }
    return request.initiator.session.post(
        f"{request.base_url}/mesh/{quote(request.federation_name, safe='')}/"
        "api/retrieval/jobs",
        json={"dataset_urn": request.dataset_urn},
        headers=headers,
        verify=request.initiator.session.verify,
        timeout=120,
    )


def _error_reason(exc: Exception) -> str | None:
    body = getattr(exc, "response_data", None) or getattr(exc, "body", None)
    if isinstance(body, Mapping):
        detail = body.get("detail", body)
        if isinstance(detail, Mapping) and isinstance(detail.get("reason"), str):
            return detail["reason"]
    return None


def _cleanup_failure(mutation: Any, exc: Exception) -> CleanupResult:
    status: Literal["absent", "failed"] = (
        "absent" if getattr(exc, "status_code", None) == 404 else "failed"
    )
    return CleanupResult(
        target_id=mutation.target_id,
        resource_type=mutation.resource_type,
        resource_id=mutation.resource_id,
        status=status,
        detail=None if status == "absent" else f"{type(exc).__name__}: cleanup failed",
    )


if __name__ == "__main__":
    raise SystemExit(main())
