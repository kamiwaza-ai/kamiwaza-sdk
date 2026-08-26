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
from kamiwaza_sdk.validation.federation_spec import (
    FEDERATION_CASE_IDS,
    FEDERATION_SCENARIO_ID,
    planned_shared_issuer,
)
from kamiwaza_sdk.validation.models import RuntimeSecretReference
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
        self.deleted: list[str] = []

    def pair(self, *, name: str, role: str, **kwargs: Any) -> dict[str, str]:
        del kwargs
        self.proxies.setdefault(name, _FederationProxy())
        return {"id": f"{role}-{self.cluster_id}-fed", "name": name}

    def get(self, federation_id: str) -> dict[str, str]:
        del federation_id
        return {"remote_cluster_id": self.remote_id}

    def __getitem__(self, name: str) -> _FederationProxy:
        return self.proxies[name]


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

    def create_owned_realm(self, realm: str, owner_nonce: str) -> dict[str, Any]:
        del owner_nonce
        return {"realm": realm, "created": True}

    def delete_owned_realm(self, realm: str, owner_nonce: str) -> bool:
        del owner_nonce
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
        self.deleted_users.append(user_id)
        return True

    def delete_client(self, realm: str, client_uuid: str) -> bool:
        del realm
        self.deleted_clients.append(client_uuid)
        return True

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
