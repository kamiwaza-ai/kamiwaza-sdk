"""Owned shared-IdP realm, edge, and fixture setup."""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from kamiwaza_sdk.validation.federation_common import (
    all_personas,
    federation_name,
    initial_tuples,
    jwt_subject,
    optional_text,
    read_execution_gate,
    required_text,
    selected_endpoints,
)
from kamiwaza_sdk.validation.federation_fixture import (
    GATE_CLASSPATH,
    GATE_NAME,
    GATE_PACKAGE_NAME,
    GATE_PACKAGE_SPEC,
    PERSONAS,
    TENANT_NEGATIVE_PERSONAS,
)
from kamiwaza_sdk.validation.federation_runtime import read_file_reference
from kamiwaza_sdk.validation.federation_spec import (
    SHARED_REALM_CLIENT_ID,
    SHARED_REALM_PERSONA_PASSWORD_REF,
)
from kamiwaza_sdk.validation.federation_state import FederationStateStore, owner_nonce
from kamiwaza_sdk.validation.federation_state import MutationSpec
from kamiwaza_sdk.validation.inference_state import runtime_ownership_key
from kamiwaza_sdk.validation.models import (
    FixtureState,
    ResolvedScenario,
    RuntimeCluster,
    RuntimeContext,
    ScenarioPlan,
)
from kamiwaza_sdk.validation.provider import ProviderContractError


_ALLOW_ALL_EXECUTION_GATE = (
    "kamiwaza.services.authz.gates.default_gates.AllowAllExecutionGate"
)
_GATE_INDEX_ENV = "KAMIWAZA_FEDERATION_GATE_INDEX_URL"
_GATE_HASH_ENV = "KAMIWAZA_FEDERATION_GATE_HASH"
_GATE_SPEC_ENV = "KAMIWAZA_FEDERATION_GATE_PACKAGE_SPEC"
_DATASET_PATH_ENV = "KAMIWAZA_FEDERATION_DATASET_PATH"
_DATASET_DEFAULT_PATH = "/app/models/kamiwaza-validation-mini-clearance.csv"


@dataclass
class RealmContext:
    state: FixtureState
    store: FederationStateStore
    plan: ScenarioPlan
    runtime: RuntimeContext
    admin: Any
    target_id: str


@dataclass
class EdgeContext:
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


def _record(
    store: FederationStateStore,
    state: FixtureState,
    target_id: str,
    resource_type: str,
    resource_id: str,
    opaque: Mapping[str, Any] | None = None,
) -> FixtureState:
    return store.record(
        state,
        MutationSpec(target_id, resource_type, resource_id),
        opaque,
    )


def prepare_realm(context: RealmContext) -> FixtureState:
    selected = context.plan.selected[0]
    params = selected.redacted_parameters
    realm = required_text(params, "realm")
    nonce = owner_nonce(runtime_ownership_key(context.runtime), realm)
    state = _record(
        context.store,
        context.state,
        context.target_id,
        "keycloak-realm",
        realm,
        {"realm": realm, "issuer": required_text(params, "issuer")},
    )
    created = context.admin.create_owned_realm(realm, nonce)
    if not created:
        raise ProviderContractError("shared-IdP realm creation returned no result")
    context.admin.set_unmanaged_attributes(realm)
    client = context.admin.ensure_ropc_client(realm, SHARED_REALM_CLIENT_ID)
    client_uuid = required_text(client, "id")
    state = _record(
        context.store, state, context.target_id, "keycloak-client", client_uuid,
        {"client_uuid": client_uuid}
    )
    for attribute in ("clearance", "tenant_id", "tenant"):
        context.admin.ensure_attribute_mapper(realm, client_uuid, attribute=attribute)
    password = _persona_password(context.runtime)
    for username, attributes in all_personas():
        user = context.admin.ensure_user(
            realm, username, password=password, attributes=attributes
        )
        user_id = required_text(user, "id")
        state = _record(
            context.store,
            state,
            context.target_id,
            "keycloak-user",
            user_id,
            {f"user:{username}": user_id},
        )
    return context.store.update_edge(
        state,
        context.target_id,
        {
            "realm": realm,
            "issuer": required_text(params, "issuer"),
            "client_id": SHARED_REALM_CLIENT_ID,
            "client_uuid": client_uuid,
            "owner_nonce_derived": True,
        },
    )


def prepare_edge(context: EdgeContext) -> FixtureState:
    _bind_edge(context)
    _configure_edge(context)
    _seed_brokered_users(context)
    return context.state


def _bind_edge(context: EdgeContext) -> None:
    context.initiator_id, context.receiver_id = selected_endpoints(context.selected)
    context.initiator = context.clusters[context.initiator_id].client
    context.receiver = context.clusters[context.receiver_id].client
    context.realm = required_text(context.params, "realm")
    context.issuer = required_text(context.params, "issuer")
    context.name = federation_name(context.runtime.run_id, context.selected.target_id)
    psk = secrets.token_urlsafe(32)
    shared = {
        "shared_issuer_url": context.issuer,
        "shared_jwks_url": context.issuer.rstrip("/")
        + "/protocol/openid-connect/certs",
    }
    receiver_record = context.receiver.federations.pair(
        name=context.name, role="receiver", preshared_key=psk, **shared
    )
    receiver_id = required_text(receiver_record, "id")
    context.state = _record(
        context.store,
        context.state,
        context.selected.target_id,
        "receiver-federation",
        receiver_id,
        {"receiver_federation_id": receiver_id},
    )
    initiator_record = context.initiator.federations.pair(
        name=context.name,
        role="initiator",
        remote_url=context.runtime_clusters[context.receiver_id].base_url,
        preshared_key=psk,
        **shared,
    )
    initiator_id = required_text(initiator_record, "id")
    context.state = _record(
        context.store,
        context.state,
        context.selected.target_id,
        "initiator-federation",
        initiator_id,
        {"initiator_federation_id": initiator_id},
    )
    context.source_cluster_id = (
        optional_text(context.receiver.federations.get(receiver_id), "remote_cluster_id")
        or context.initiator_id
    )
    context.resolved_receiver_id = (
        optional_text(context.initiator.federations.get(initiator_id), "remote_cluster_id")
        or context.receiver_id
    )
    if context.source_cluster_id == context.resolved_receiver_id:
        raise ProviderContractError("shared-IdP pairing resolved one cluster identity")


def _configure_edge(context: EdgeContext) -> None:
    for client in (context.initiator, context.receiver):
        client.cluster.declare_attribute("clearance", type="string")
    if _ensure_gate(context.receiver):
        context.state = _record(
            context.store,
            context.state,
            context.selected.target_id,
            "gate-package",
            GATE_PACKAGE_NAME,
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
    context.state = _record(
        context.store,
        context.state,
        context.selected.target_id,
        "dataset",
        context.urn,
        {"dataset_urn": context.urn},
    )
    context.receiver.datasets.set_gate(context.urn, type=GATE_CLASSPATH, config={})
    previous_gate = read_execution_gate(context.receiver)
    context.receiver.cluster.set_execution_gate(type=_ALLOW_ALL_EXECUTION_GATE, config={})
    context.state = _record(
        context.store,
        context.state,
        context.selected.target_id,
        "execution-gate",
        context.receiver_id,
        {"previous_execution_gate": _json_value(previous_gate)},
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


def _seed_brokered_users(context: EdgeContext) -> None:
    password_ref = context.runtime.secret_refs.get(SHARED_REALM_PERSONA_PASSWORD_REF)
    if not password_ref:
        raise ProviderContractError("shared-IdP persona password reference is missing")
    password = read_file_reference(password_ref, label="shared-IdP persona password")
    for clearance, username in PERSONAS.items():
        context.state = _seed_brokered_user(
            context,
            username,
            password,
            initial_tuples(context.urn, job_executor=clearance == "U"),
        )
    for case_name, (username, _attrs) in TENANT_NEGATIVE_PERSONAS.items():
        context.state = _seed_brokered_user(context, username, password, [], label=case_name)


def _seed_brokered_user(
    context: EdgeContext,
    username: str,
    password: str,
    tuples: list[dict[str, str]],
    *,
    label: str | None = None,
) -> FixtureState:
    token = context.admin.ropc_token(
        context.realm, SHARED_REALM_CLIENT_ID, username, password
    )
    external_id = f"{jwt_subject(token)}@{context.source_cluster_id}"
    context.receiver.federations[context.name].users.add(external_id, initial_tuples=tuples)
    resource_label = label or username
    return _record(
        context.store,
        context.state,
        context.selected.target_id,
        "brokered-user",
        external_id,
        {f"brokered:{resource_label}": external_id},
    )


def _ensure_gate(receiver: Any) -> bool:
    installed = _find_gate_package(receiver.gates.packages.list())
    if installed is None:
        _install_gate_package(receiver)
    _validate_gate_discovery(receiver)
    return installed is None


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
        raise ProviderContractError("shared-IdP gate classpath resolved unexpected gate")


def _persona_password(runtime: RuntimeContext) -> str:
    reference = runtime.secret_refs.get(SHARED_REALM_PERSONA_PASSWORD_REF)
    if not reference:
        raise ProviderContractError("shared-IdP persona password reference is missing")
    return read_file_reference(reference, label="shared-IdP persona password")


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (dict, list, str, int, float, bool)):
        return value
    return str(value)
