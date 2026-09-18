"""Contract tests for the SDK-owned shared-IdP provider."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kamiwaza_sdk.validation import (
    RuntimeContext,
    ValidationProfile,
)
from kamiwaza_sdk.validation import federation_cases as case_module
from kamiwaza_sdk.validation import federation_provider as provider_module
from kamiwaza_sdk.validation import (
    model_digest,
)
from kamiwaza_sdk.validation.federation_fixture import (
    KNOWN,
    PERSONAS,
    TENANT_NEGATIVE_PERSONAS,
    UNONBOARDED_PERSONA,
    records,
)
from kamiwaza_sdk.validation.federation_provider import (
    FEDERATION_PROVIDER_REVISION,
    FederationLifecycleProvider,
)
from kamiwaza_sdk.validation.federation_runtime import KeycloakAdminFactory
from kamiwaza_sdk.validation.federation_spec import (
    FEDERATION_CASE_IDS,
    FEDERATION_SCENARIO_ID,
    planned_shared_issuer,
)
from kamiwaza_sdk.validation.models import RuntimeSecretReference
from kamiwaza_sdk.validation.provider import ProviderContractError
from kamiwaza_sdk.validation.testkit import RecordingFixtureStateWriter
from tests.contract.validation.federation_cluster_fakes import _ClusterFactory
from tests.contract.validation.federation_idp_fakes import _Admin, _AdminFactory
from tests.contract.validation.federation_test_support import _profile, _runtime
from tests.contract.validation.support import profile_payload

pytestmark = pytest.mark.contract


def test_provider_records_match_the_canonical_integration_fixture() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "integration"
        / "fixtures"
        / "mini_access_tier_records.json"
    )

    assert list(records()) == json.loads(fixture_path.read_text(encoding="utf-8"))


def test_provider_mesh_retrieval_explicitly_requests_sse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []
    diagnostic_calls: list[tuple] = []

    class Persona:
        session = SimpleNamespace(verify=True)

        def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, str]:
            calls.append((method, path, kwargs))
            return {"job_id": "job-1"}

    class Response:
        closed = False
        status_code = 200

        def iter_lines(self, *, decode_unicode: bool) -> list[str]:
            assert decode_unicode is True
            return []

        def close(self) -> None:
            self.closed = True

    response = Response()
    monkeypatch.setattr(
        case_module,
        "federation_credential_headers",
        lambda _federation_name: {"X-Kamiwaza-Federation-Credential": "test"},
    )
    monkeypatch.setattr(case_module, "_retrieval_stream", lambda _request: response)
    monkeypatch.setattr(
        case_module,
        "log_missing_audit_job_state",
        lambda *args: diagnostic_calls.append(args),
    )

    assert case_module._mesh_retrieve(
        case_module.RetrievalRequest(
            persona=Persona(),
            base_url="https://edge-a.test/api",
            token="token",
            federation_name="peer",
            dataset_urn="urn:test",
        )
    ) == ([], [])
    assert calls == [
        (
            "POST",
            "/mesh/peer/api/retrieval/jobs",
            {
                "json": {"dataset_urn": "urn:test", "transport": "sse"},
                "headers": {"X-Kamiwaza-Federation-Credential": "test"},
            },
        ),
    ]
    assert diagnostic_calls == [
        (
            "https://edge-a.test/api/mesh/peer/api/retrieval/jobs/job-1",
            {
                "Authorization": "Bearer token",
                "X-Kamiwaza-Federation-Credential": "test",
            },
            True,
            [],
        )
    ]
    assert response.closed


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
    monkeypatch.setenv("KAMIWAZA_FEDERATION_GATE_HASH", "sha256:" + "0" * 64)
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
    monkeypatch.setenv("KAMIWAZA_FEDERATION_GATE_HASH", "sha256:" + "0" * 64)
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
        access_tier = next(key for key, value in PERSONAS.items() if value == username)
        count, allowed = KNOWN[access_tier]
        return [row for row in records() if row["tier"] in allowed][:count], [
            {"gate": "mini_access_tier_gate"}
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
    assert tuple(PERSONAS.values()) == (
        "fed-tier-public",
        "fed-tier-private",
        "fed-tier-confidential",
    )
    assert UNONBOARDED_PERSONA == "fed-tier-unonboarded"
