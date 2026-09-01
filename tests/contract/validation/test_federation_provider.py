"""Contract tests for the SDK-owned shared-IdP provider."""

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
    model_digest,
)
from kamiwaza_sdk.validation.federation_fixture import (
    GATE_CLASSPATH,
    KNOWN,
    PERSONAS,
    TENANT_NEGATIVE_PERSONAS,
    UNONBOARDED_PERSONA,
    records,
)
from kamiwaza_sdk.validation import federation_provider as provider_module
from kamiwaza_sdk.validation.federation_provider import (
    FEDERATION_PROVIDER_REVISION,
    FederationLifecycleProvider,
)
from kamiwaza_sdk.validation.federation_runtime import (
    KeycloakAdminFactory,
    KeycloakTokenClient,
)
from kamiwaza_sdk.validation.federation_state import sign_state
from kamiwaza_sdk.validation.inference_state import runtime_ownership_key
from kamiwaza_sdk.validation.federation_spec import (
    FEDERATION_CASE_IDS,
    FEDERATION_SCENARIO_ID,
    SHARED_REALM_EXTERNAL_CLIENT_ID_REF,
    planned_shared_issuer,
)
from kamiwaza_sdk.validation.models import FixtureMutation, RuntimeSecretReference
from kamiwaza_sdk.validation.provider import ProviderContractError
from kamiwaza_sdk.validation.testkit import RecordingFixtureStateWriter
from tests.contract.validation.support import profile_payload

pytestmark = pytest.mark.contract


def _profile() -> ValidationProfile:
    payload = profile_payload()
    payload["validation"] = {
        "level": "smoke",
        "fixture_mode": "owned",
        "include": [FEDERATION_SCENARIO_ID],
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
            "roles": ["controller"],
            "node_count": 1,
            "hardware": {"accelerators": []},
            "features": {"rebac": True},
        },
    ]
    payload["mesh"] = {
        "edges": [
            {"initiator": "edge-a", "receiver": "edge-b", "identity_mode": "shared_idp"}
        ]
    }
    payload.pop("inference_targets", None)
    return ValidationProfile.model_validate(payload)


def test_provider_records_match_the_canonical_integration_fixture() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "integration"
        / "fixtures"
        / "mini_clearance_records.json"
    )

    assert list(records()) == json.loads(fixture_path.read_text(encoding="utf-8"))


def _runtime(tmp_path: Path) -> RuntimeContext:
    ownership = tmp_path / "ownership.key"
    ownership.write_bytes(b"o" * 48)
    ownership.chmod(0o600)
    password = tmp_path / "persona.password"
    password.write_text("persona-secret\n", encoding="utf-8")
    return RuntimeContext.model_validate(
        {
            "schema": "kamiwaza.runtime-context/v1",
            "run_id": "run-federation-1",
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


def _external_profile() -> ValidationProfile:
    payload = _profile().model_dump(mode="json")
    payload["validation"]["fixture_mode"] = "external"  # type: ignore[index]
    return ValidationProfile.model_validate(payload)


def _external_runtime(tmp_path: Path) -> RuntimeContext:
    runtime = _runtime(tmp_path)
    client_id = tmp_path / "external-client-id"
    client_id.write_text("customer-shared-cli\n", encoding="utf-8")
    return runtime.model_copy(
        update={
            "secret_refs": {
                SHARED_REALM_EXTERNAL_CLIENT_ID_REF: client_id.as_uri(),
                "shared-idp-persona-password": runtime.secret_refs[
                    "shared-idp-persona-password"
                ],
            }
        }
    )


def _jwt(subject: str) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = (
        base64.urlsafe_b64encode(json.dumps({"sub": subject}).encode())
        .decode()
        .rstrip("=")
    )
    return f"{header}.{payload}.signature"


class _NotFound(RuntimeError):
    status_code = 404


class _Users:
    def __init__(self) -> None:
        self.added: list[str] = []

    def add(
        self, external_id: str, *, initial_tuples: list[dict[str, str]]
    ) -> dict[str, str]:
        del initial_tuples
        self.added.append(external_id)
        return {"id": external_id}


class _FederationProxy:
    def __init__(self) -> None:
        self.users = _Users()


class _Federations:
    def __init__(self, cluster_id: str, remote_id: str) -> None:
        self.cluster_id = cluster_id
        self.remote_id = remote_id
        self.proxies: dict[str, _FederationProxy] = {}
        self.id_proxies: dict[str, _FederationProxy] = {}
        self.deleted: list[str] = []
        self.revoked: set[str] = set()

    def pair(self, *, name: str, role: str, **kwargs: Any) -> dict[str, str]:
        del kwargs
        proxy = self.proxies.setdefault(name, _FederationProxy())
        federation_id = f"{role}-{self.cluster_id}-fed"
        self.id_proxies[federation_id] = proxy
        return {"id": federation_id, "name": name}

    def get(self, federation_id: str) -> dict[str, str]:
        del federation_id
        return {"remote_cluster_id": self.remote_id}

    def __getitem__(self, name: str) -> _FederationProxy:
        return self.proxies[name]

    def by_id(
        self, federation_id: str, *, remote_name: str | None = None
    ) -> _FederationProxy:
        del remote_name
        return self.id_proxies[federation_id]


class _ClusterAPI:
    def __init__(self) -> None:
        self.execution_gate_calls: list[tuple[str, dict[str, Any]]] = []

    def declare_attribute(self, name: str, *, type: str) -> None:
        del name, type

    def get_execution_gate(self) -> Any:
        raise _NotFound("no execution gate")

    def set_execution_gate(self, *, type: str, config: dict[str, Any]) -> None:
        self.execution_gate_calls.append((type, config))

    def clear_execution_gate(self) -> None:
        self.execution_gate_calls.append(("clear", {}))


class _Packages:
    def list(self) -> list[Any]:
        return [SimpleNamespace(name="acme-gates", classpaths=[GATE_CLASSPATH])]

    def uninstall(self, package_name: str) -> None:
        del package_name


class _Gates:
    def __init__(self) -> None:
        self.packages = _Packages()

    def discover(self, classpath: str) -> Any:
        assert classpath == GATE_CLASSPATH
        return SimpleNamespace(name="mini_clearance_gate")


class _Datasets:
    def __init__(self) -> None:
        self.created: list[str] = []

    def create(self, **kwargs: Any) -> str:
        del kwargs
        urn = "urn:li:dataset:(urn:li:dataPlatform:file,/tmp/clearance,PROD)"
        self.created.append(urn)
        return urn

    def set_gate(self, urn: str, *, type: str, config: dict[str, Any]) -> None:
        del urn, type, config

    def delete(self, urn: str) -> None:
        if urn not in self.created:
            raise _NotFound("dataset is already absent")
        self.created.remove(urn)


class _Client:
    def __init__(self, cluster_id: str, remote_id: str) -> None:
        self.cluster_id = cluster_id
        self.federations = _Federations(cluster_id, remote_id)
        self.datasets = _Datasets()
        self.gates = _Gates()
        self.cluster = _ClusterAPI()
        self.requests: list[tuple[str, str]] = []

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        if method in {"DELETE", "POST"} and any(
            old_method == method and old_path == path
            for old_method, old_path in self.requests
        ):
            raise _NotFound("federation is already absent")
        self.requests.append((method, path))
        return {}

    def close(self) -> None:
        return None


class _ClusterWrapper:
    def __init__(self, client: _Client) -> None:
        self.client = client

    def close(self) -> None:
        self.client.close()


class _ClusterFactory:
    def __init__(self) -> None:
        self.clients = {
            "edge-a": _ClusterWrapper(_Client("edge-a", "edge-b")),
            "edge-b": _ClusterWrapper(_Client("edge-b", "edge-a")),
        }

    def __call__(self, runtime_cluster: Any) -> _ClusterWrapper:
        return self.clients[str(runtime_cluster.id)]


class _Admin:
    def __init__(self) -> None:
        self.deleted_users: list[str] = []
        self.deleted_clients: list[str] = []
        self.deleted_realms: list[str] = []
        self.token_client_ids: list[str] = []

    def create_owned_realm(self, realm: str, owner_nonce: str) -> dict[str, Any]:
        del owner_nonce
        return {"realm": realm, "created": True}

    def delete_owned_realm(self, realm: str, owner_nonce: str) -> bool:
        del owner_nonce
        if realm in self.deleted_realms:
            raise _NotFound("realm is already absent")
        self.deleted_realms.append(realm)
        return True

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

    def delete_user(self, realm: str, user_id: str) -> bool:
        del realm
        if user_id in self.deleted_users:
            raise _NotFound("user is already absent")
        self.deleted_users.append(user_id)
        return True

    def delete_client(self, realm: str, client_uuid: str) -> bool:
        del realm
        if client_uuid in self.deleted_clients:
            raise _NotFound("client is already absent")
        self.deleted_clients.append(client_uuid)
        return True

    def ropc_token(
        self, realm: str, client_id: str, username: str, password: str
    ) -> str:
        del realm, password
        self.token_client_ids.append(client_id)
        return _jwt(username)


class _AdminFactory:
    def __init__(self, admin: _Admin) -> None:
        self.admin = admin

    def __call__(self, runtime: RuntimeContext, cluster: Any) -> _Admin:
        del runtime, cluster
        return self.admin


class _RunPersona:
    def __init__(self, token: str, target_id: str, dataset_urn: str) -> None:
        self.token = token
        self.target_id = target_id
        self.dataset_urn = dataset_urn
        self.session = SimpleNamespace(verify=True)
        self.catalog = SimpleNamespace(
            datasets=SimpleNamespace(
                list=lambda *, target_cluster: [SimpleNamespace(urn=self.dataset_urn)]
            )
        )
        self.jobs = SimpleNamespace(run=self._run_job)

    def _run_job(self, *, entrypoint: str, **kwargs: Any) -> Any:
        del kwargs
        assert "python3 -c" in entrypoint
        return SimpleNamespace(
            status="SUCCEEDED",
            result={"probe": f"kamiwaza-validation-{self.target_id}"},
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        del kwargs
        if method == "GET" and path.endswith("/diagnose"):
            error = RuntimeError("not brokered")
            error.status_code = 403  # type: ignore[attr-defined]
            error.response_data = {  # type: ignore[attr-defined]
                "detail": {"reason": "unauthorized_brokered_user"}
            }
            raise error
        raise AssertionError("unexpected persona request")


def test_resolution_is_deterministic_and_publishes_issuer_trust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAMIWAZA_SHARED_IDP_PUBLIC_URL", "https://idp.test")
    monkeypatch.setenv("KAMIWAZA_VALIDATION_RUN_ID", "run-federation-1")
    profile = _profile()
    provider = FederationLifecycleProvider()

    first = provider.resolve(profile)
    second = provider.resolve(profile)

    assert first == second
    assert first.provider_revision == FEDERATION_PROVIDER_REVISION
    assert first.selected[0].case_ids == FEDERATION_CASE_IDS
    assert first.selected[0].cluster_ids == ("edge-a", "edge-b")
    assert first.install_requirements["scheduler"]["trustedSharedIssuers"] == [
        first.selected[0].redacted_parameters["issuer"]
    ]
    assert planned_shared_issuer(profile).startswith(
        "https://idp.test/realms/kz-validation-"
    )


def test_external_resolution_uses_customer_issuer_and_advertises_external_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "KAMIWAZA_SHARED_IDP_EXTERNAL_ISSUER",
        "https://customer-idp.test/realms/shared",
    )

    provider = FederationLifecycleProvider()
    plan = provider.resolve(_external_profile())

    assert "external" in provider.describe()[0].fixture_modes
    assert plan.selected[0].redacted_parameters == {
        "issuer": "https://customer-idp.test/realms/shared",
        "realm": "shared",
        "client_id_ref": SHARED_REALM_EXTERNAL_CLIENT_ID_REF,
        "persona_usernames": [
            "fed-clr-u",
            "fed-clr-s",
            "fed-clr-ts",
            "fed-clr-unonboarded",
            "fed-tenant-missing",
            "fed-tenant-legacy-only",
            "fed-tenant-nondefault",
        ],
        "fixture_mode": "external",
    }


def test_external_prepare_never_mutates_idp_and_uses_external_client_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "KAMIWAZA_SHARED_IDP_EXTERNAL_ISSUER",
        "https://customer-idp.test/realms/shared",
    )
    factory = _ClusterFactory()
    admin = _Admin()
    provider = FederationLifecycleProvider(
        cluster_factory=factory,
        admin_factory=_AdminFactory(admin),
    )
    runtime = _external_runtime(tmp_path)
    plan = provider.resolve(_external_profile())

    state = provider.prepare(plan, runtime, RecordingFixtureStateWriter())

    assert not admin.deleted_realms
    assert not admin.deleted_clients
    assert not admin.deleted_users
    assert set(admin.token_client_ids) == {"customer-shared-cli"}
    assert not any(item.resource_type.startswith("keycloak-") for item in state.journal)
    assert state.opaque["ownership"]["scheme"] == "kamiwaza.validation/v1"  # type: ignore[index]
    edge = state.opaque["edges"][plan.selected[0].target_id]  # type: ignore[index]
    assert edge["fixture_mode"] == "external"  # type: ignore[index]


def test_teardown_is_idempotent_after_resources_are_already_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KAMIWAZA_SHARED_IDP_PUBLIC_URL", "https://idp.test")
    monkeypatch.setenv("KAMIWAZA_VALIDATION_RUN_ID", "run-federation-1")
    factory = _ClusterFactory()
    admin = _Admin()
    provider = FederationLifecycleProvider(
        cluster_factory=factory,
        admin_factory=_AdminFactory(admin),
    )
    runtime = _runtime(tmp_path)
    state = provider.prepare(
        provider.resolve(_profile()), runtime, RecordingFixtureStateWriter()
    )

    first = provider.teardown(runtime, state)
    second = provider.teardown(runtime, state)

    assert first.status == "passed"
    assert second.status == "passed"
    assert all(item.status != "failed" for item in second.results)
    assert "absent" in {item.status for item in second.results}


def test_legacy_owned_state_without_provider_tag_remains_reconcilable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KAMIWAZA_SHARED_IDP_PUBLIC_URL", "https://idp.test")
    monkeypatch.setenv("KAMIWAZA_VALIDATION_RUN_ID", "run-federation-1")
    factory = _ClusterFactory()
    admin = _Admin()
    provider = FederationLifecycleProvider(
        cluster_factory=factory,
        admin_factory=_AdminFactory(admin),
    )
    runtime = _runtime(tmp_path)
    state = provider.prepare(
        provider.resolve(_profile()), runtime, RecordingFixtureStateWriter()
    )
    opaque = dict(state.opaque)
    opaque.pop("ownership", None)
    edges = {}
    for target_id, edge in state.opaque["edges"].items():  # type: ignore[index]
        legacy_edge = dict(edge)
        legacy_edge.pop("ownership", None)
        edges[target_id] = legacy_edge
    opaque["edges"] = edges
    legacy = sign_state(
        state.model_copy(update={"opaque": opaque}), runtime_ownership_key(runtime)
    )

    cleanup = provider.teardown(runtime, legacy)

    assert cleanup.status == "passed"
    assert all(item.status != "failed" for item in cleanup.results)


def test_cleanup_rejects_a_foreign_ownership_tag_before_deleting(
    tmp_path: Path,
) -> None:
    from kamiwaza_sdk.validation.federation_cleanup import (
        CleanupContext,
        cleanup_mutation,
    )

    mutation = FixtureMutation(
        sequence=1,
        target_id="edge",
        resource_type="receiver-federation",
        resource_id="foreign-id",
        action="created",
    )
    context = CleanupContext(
        resources={
            "ownership": {
                "scheme": "kamiwaza.validation/v1",
                "owner": "sha256:" + "f" * 64,
            }
        },
        receiver=SimpleNamespace(
            _request=lambda *args, **kwargs: pytest.fail("deleted foreign resource")
        ),
        admin=None,
        runtime=_runtime(tmp_path),
    )

    with pytest.raises(RuntimeError, match="ownership metadata"):
        cleanup_mutation(mutation, context)


@pytest.mark.parametrize(
    "public_url",
    (
        "https://idp.test/realm",
        "https://idp.test?tenant=one",
        "https://idp.test/#fragment",
    ),
)
def test_resolution_rejects_non_origin_public_url(
    public_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KAMIWAZA_SHARED_IDP_PUBLIC_URL", public_url)
    monkeypatch.setenv("KAMIWAZA_VALIDATION_RUN_ID", "run-federation-1")

    with pytest.raises(ProviderContractError, match="HTTPS origin"):
        planned_shared_issuer(_profile())


def test_resolution_without_edges_is_empty_and_does_not_require_idp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = profile_payload()
    payload["validation"] = {
        "level": "smoke",
        "fixture_mode": "owned",
        "include": [],
        "exclude": [],
    }
    profile = ValidationProfile.model_validate(payload)
    monkeypatch.delenv("KAMIWAZA_SHARED_IDP_PUBLIC_URL", raising=False)

    plan = FederationLifecycleProvider().resolve(profile)

    assert plan.selected == ()
    assert plan.install_requirements == {}


def test_prepare_and_teardown_journal_every_owned_resource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KAMIWAZA_SHARED_IDP_PUBLIC_URL", "https://idp.test")
    monkeypatch.setenv("KAMIWAZA_VALIDATION_RUN_ID", "run-federation-1")
    factory = _ClusterFactory()
    admin = _Admin()
    provider = FederationLifecycleProvider(
        cluster_factory=factory,
        admin_factory=_AdminFactory(admin),
    )
    profile = _profile()
    runtime = _runtime(tmp_path)
    plan = provider.resolve(profile)
    writer = RecordingFixtureStateWriter()

    state = provider.prepare(plan, runtime, writer)
    cleanup = provider.teardown(runtime, state)

    expected_journal = (
        1
        + 1
        + len(PERSONAS)
        + 1
        + len(TENANT_NEGATIVE_PERSONAS)
        + 2
        + 1
        + 1
        + len(PERSONAS)
        + len(TENANT_NEGATIVE_PERSONAS)
    )
    assert len(state.journal) == expected_journal
    assert state.journal[0].resource_type == "keycloak-realm"
    assert all("ownership_mac" in snapshot.opaque for snapshot in writer.snapshots)
    assert cleanup.status == "passed"
    assert {item.status for item in cleanup.results} == {"removed"}
    assert admin.deleted_realms
    assert len(admin.deleted_users) == len(PERSONAS) + 1 + len(TENANT_NEGATIVE_PERSONAS)
    assert any(
        method == "DELETE" and path.startswith("/cluster/federations/")
        for method, path in factory.clients["edge-a"].client.requests
    )
    assert any(
        method == "DELETE" and path.startswith("/cluster/federations/")
        for method, path in factory.clients["edge-b"].client.requests
    )


def test_run_emits_all_nine_cases_with_redacted_failure_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KAMIWAZA_SHARED_IDP_PUBLIC_URL", "https://idp.test")
    monkeypatch.setenv("KAMIWAZA_VALIDATION_RUN_ID", "run-federation-1")
    factory = _ClusterFactory()
    admin = _Admin()
    provider = FederationLifecycleProvider(
        cluster_factory=factory,
        admin_factory=_AdminFactory(admin),
    )
    runtime = _runtime(tmp_path)
    plan = provider.resolve(_profile())
    writer = RecordingFixtureStateWriter()
    state = provider.prepare(plan, runtime, writer)
    selected = plan.selected[0]
    edge = dict(state.opaque["edges"][selected.target_id])  # type: ignore[index]
    params = dict(edge)

    def fake_client(base_url: str, token: str) -> _RunPersona:
        del base_url
        return _RunPersona(token, selected.target_id, params["dataset_urn"])

    def fake_retrieve(
        request: Any,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        username = provider_module._jwt_subject(request.token)
        clearance = next(key for key, value in PERSONAS.items() if value == username)
        count, allowed = KNOWN[clearance]
        return [row for row in records() if row["classification"] in allowed][:count], [
            {"gate": "mini_clearance_gate"}
        ]

    monkeypatch.setattr(provider_module, "_token_client", fake_client)
    monkeypatch.setattr(provider_module, "_mesh_retrieve", fake_retrieve)
    monkeypatch.setattr(provider_module, "_assert_tenant_denial", lambda request: None)

    evidence = provider.run(plan, runtime, state)

    assert [item.case_id for item in evidence.results] == list(FEDERATION_CASE_IDS)
    assert all(item.status == "passed" for item in evidence.results)
    assert evidence.state_digest == model_digest(state)


def test_runtime_secret_reference_is_exported_without_exposing_repr() -> None:
    assert RuntimeSecretReference
    runtime = RuntimeContext.model_validate(
        {
            "schema": "kamiwaza.runtime-context/v1",
            "run_id": "run-secret-repr",
            "clusters": [
                {
                    "id": "c1",
                    "base_url": "https://c1.test/api",
                    "api_key_ref": "file:///tmp/api.key",
                    "kubeconfig_ref": "file:///tmp/kubeconfig",
                }
            ],
            "secret_refs": {"admin": "file:///tmp/admin.password"},
        }
    )
    assert "admin.password" not in repr(runtime)
    assert model_digest(runtime).startswith("sha256:")


def test_keycloak_admin_factory_honors_sdk_tls_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The provider's Keycloak channel must share the SDK TLS policy.

    Existing validation clusters use a self-signed ingress certificate, so the
    documented ``KAMIWAZA_VERIFY_SSL=false`` setting must reach the admin
    client.  A hard-coded ``verify=True`` makes the provider fail before it can
    create its owned realm.
    """
    admin_password = tmp_path / "admin.password"
    admin_password.write_text("admin-secret\n", encoding="utf-8")
    runtime = _runtime(tmp_path).model_copy(
        update={
            "secret_refs": {
                "shared-idp-admin-password": admin_password.as_uri(),
            }
        }
    )
    captured: dict[str, Any] = {}

    class _StubAdmin:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["args"] = args
            captured.update(kwargs)

    import kamiwaza_sdk.seeding.federation.keycloak as keycloak_module

    monkeypatch.setattr(keycloak_module, "KeycloakAdmin", _StubAdmin)
    monkeypatch.setenv("KAMIWAZA_SHARED_IDP_ADMIN_URL", "https://idp.test")
    monkeypatch.setenv("KAMIWAZA_VERIFY_SSL", "false")

    KeycloakAdminFactory()(runtime, runtime.clusters[0])

    assert captured["verify"] is False


def test_external_token_client_uses_issuer_without_admin_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _Response:
        status_code = 200

        def json(self) -> dict[str, str]:
            return {"access_token": "jwt-token"}

    def post(url: str, **kwargs: Any) -> _Response:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _Response()

    import requests

    monkeypatch.setattr(requests, "post", post)

    token = KeycloakTokenClient(
        "https://customer-idp.test/realms/shared", verify=False
    ).ropc_token("ignored", "customer-cli", "fed-clr-u", "persona-secret")

    assert token == "jwt-token"
    assert captured["url"] == (
        "https://customer-idp.test/realms/shared/protocol/openid-connect/token"
    )
    assert captured["kwargs"]["verify"] is False
    assert captured["kwargs"]["data"]["client_id"] == "customer-cli"


def test_explicit_scenario_without_mesh_edge_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = profile_payload()
    payload["validation"] = {
        "level": "smoke",
        "fixture_mode": "owned",
        "include": [FEDERATION_SCENARIO_ID],
        "exclude": [],
    }
    profile = ValidationProfile.model_validate(payload)
    monkeypatch.setenv("KAMIWAZA_SHARED_IDP_PUBLIC_URL", "https://idp.test")

    with pytest.raises(ProviderContractError, match="no compatible mesh edge"):
        FederationLifecycleProvider().resolve(profile)


def test_all_persona_fixture_names_are_stable() -> None:
    assert tuple(PERSONAS.values()) == ("fed-clr-u", "fed-clr-s", "fed-clr-ts")
    assert UNONBOARDED_PERSONA == "fed-clr-unonboarded"
