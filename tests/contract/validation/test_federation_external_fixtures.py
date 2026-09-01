"""External-identity and crash-recovery contracts for shared-IDP federation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kamiwaza_sdk.validation import RuntimeContext, ValidationProfile
from kamiwaza_sdk.validation.federation_cleanup import CleanupContext, cleanup_mutation
from kamiwaza_sdk.validation.federation_provider import FederationLifecycleProvider
from kamiwaza_sdk.validation.federation_runtime import KeycloakTokenClient
from kamiwaza_sdk.validation.federation_spec import SHARED_REALM_EXTERNAL_CLIENT_ID_REF
from kamiwaza_sdk.validation.federation_state import sign_state
from kamiwaza_sdk.validation.inference_state import runtime_ownership_key
from kamiwaza_sdk.validation.models import FixtureMutation
from kamiwaza_sdk.validation.provider import ProviderContractError
from kamiwaza_sdk.validation.testkit import RecordingFixtureStateWriter
from tests.contract.validation.test_federation_provider import (
    _Admin,
    _AdminFactory,
    _ClusterFactory,
    _profile,
    _runtime,
)

pytestmark = pytest.mark.contract


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


def test_external_resolution_rejects_non_realm_issuer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAMIWAZA_SHARED_IDP_EXTERNAL_ISSUER", "https://idp.test")
    with pytest.raises(ProviderContractError, match="realms/<realm>"):
        FederationLifecycleProvider().resolve(_external_profile())
