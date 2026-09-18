"""Provider implementation revisions guard replay without changing wire schemas."""

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from kamiwaza_sdk.validation.delegated_workload_provider import (
    DelegatedWorkloadLifecycleProvider,
)
from kamiwaza_sdk.validation.delegated_workload_spec import DELEGATED_FEATURE_ID
from kamiwaza_sdk.validation.federation_provider import FederationLifecycleProvider
from kamiwaza_sdk.validation.federation_spec import FEDERATION_OWNERSHIP_SCHEME
from kamiwaza_sdk.validation.federation_state import (
    FederationStateStore,
    MutationSpec,
    validate_state,
)
from kamiwaza_sdk.validation.inference_state import runtime_ownership_key
from kamiwaza_sdk.validation.model_mesh_provider import ModelMeshLifecycleProvider
from kamiwaza_sdk.validation.models import FixtureState, RuntimeContext, ScenarioPlan
from kamiwaza_sdk.validation.provider import ProviderContractError
from kamiwaza_sdk.validation.testkit import RecordingFixtureStateWriter
from tests.contract.validation.federation_test_support import _profile, _runtime
from tests.contract.validation.test_delegated_workload_provider import (
    _profile as _delegated_profile,
)
from tests.contract.validation.test_model_mesh_spec import _profile as _mesh_profile

pytestmark = pytest.mark.contract
OLD_REVISION = "sdk.federation.shared-idp@v1"


@pytest.fixture
def current_provider(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KAMIWAZA_SHARED_IDP_PUBLIC_URL", "https://idp.test")
    factory = Mock(side_effect=AssertionError("must refuse before opening clients"))
    return (
        FederationLifecycleProvider(cluster_factory=factory, admin_factory=factory),
        factory,
    )


def test_new_revision_keeps_scenario_and_wire_protocol(current_provider) -> None:
    provider, _ = current_provider
    plan = provider.resolve(_profile())
    descriptor = provider.describe()[0]
    assert plan.provider_revision == "sdk.federation.shared-idp@v2"
    assert descriptor.scenario_id == "sdk.federation.shared-idp/v1"
    assert descriptor.protocol_version == "v1"
    assert FEDERATION_OWNERSHIP_SCHEME == "kamiwaza.validation/v1"
    assert plan.model_dump(by_alias=True)["schema"] == "kamiwaza.scenario-plan/v1"


@pytest.mark.parametrize("operation", ["prepare", "run"])
def test_old_plan_refused_before_clients_or_journal(
    current_provider,
    tmp_path: Path,
    operation: str,
) -> None:
    provider, factory = current_provider
    plan = provider.resolve(_profile()).model_copy(
        update={"provider_revision": OLD_REVISION}
    )
    runtime = _runtime(tmp_path)
    writer = RecordingFixtureStateWriter()
    with pytest.raises(ProviderContractError, match="provider revision mismatch"):
        if operation == "prepare":
            provider.prepare(plan, runtime, writer)
        else:
            provider.run(plan, runtime, None)  # type: ignore[arg-type]
    factory.assert_not_called()
    assert writer.snapshots == []


@pytest.mark.parametrize("operation", ["run", "teardown"])
def test_signed_old_state_refused_before_clients_or_mutation(
    current_provider,
    tmp_path: Path,
    operation: str,
) -> None:
    provider, factory = current_provider
    plan = provider.resolve(_profile())
    runtime = _runtime(tmp_path)
    old_plan = plan.model_copy(update={"provider_revision": OLD_REVISION})
    writer = RecordingFixtureStateWriter()
    state = FederationStateStore(
        writer, runtime_ownership_key(runtime), OLD_REVISION
    ).initial(old_plan, runtime)
    before = state.model_dump(mode="json")
    with pytest.raises(
        ProviderContractError, match="fixture state provider revision mismatch"
    ):
        if operation == "run":
            provider.run(plan, runtime, state)
        else:
            provider.teardown(runtime, state)
    factory.assert_not_called()
    assert state.model_dump(mode="json") == before


@pytest.fixture(params=["model-mesh", "delegated-workload"])
def related_provider(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> tuple[FederationLifecycleProvider, Mock, ScenarioPlan]:
    monkeypatch.setenv("KAMIWAZA_SHARED_IDP_PUBLIC_URL", "https://idp.test")
    monkeypatch.setenv(
        "KAMIWAZA_DELEGATED_TEST_PACKAGES_JSON",
        '["humanize==4.13.0", "kamiwaza-sdk==1.1.0"]',
    )
    monkeypatch.setenv(
        "KAMIWAZA_DELEGATED_TEST_IMPORTS_JSON", '["humanize", "kamiwaza_sdk"]'
    )
    factory = Mock(side_effect=AssertionError("must refuse before opening clients"))
    if request.param == "model-mesh":
        provider = ModelMeshLifecycleProvider(
            cluster_factory=factory, admin_factory=factory, inference_factory=factory
        )
        profile = _mesh_profile()
    else:
        provider = DelegatedWorkloadLifecycleProvider(
            cluster_factory=factory, admin_factory=factory
        )
        profile = _delegated_profile(include=True)
    plan = provider.resolve(profile)
    assert plan.selected
    return provider, factory, plan


def _serialized_legacy_plan(plan: ScenarioPlan) -> ScenarioPlan:
    """Use neutral retired identities to test revision rejection, not private replay."""
    payload = plan.model_dump(mode="json", by_alias=True)
    payload["provider_revision"] = plan.provider_revision.split("@")[0] + "@v1"
    for selected in payload["selected"]:
        selected["redacted_parameters"]["persona_usernames"] = [
            "retired-fixture-reader-1",
            "retired-fixture-reader-2",
            "retired-fixture-reader-3",
            "retired-fixture-unonboarded",
            "fed-tenant-missing",
            "fed-tenant-legacy-only",
            "fed-tenant-nondefault",
        ]
    return ScenarioPlan.model_validate_json(json.dumps(payload))


def test_related_revision_preserves_wire_and_scenario_contract(
    related_provider,
) -> None:
    provider, _, plan = related_provider
    descriptor = provider.describe()[0]
    assert plan.provider_revision == descriptor.provider_id + "@v2"
    assert descriptor.scenario_id == descriptor.provider_id + "/v1"
    assert descriptor.protocol_version == "v1"
    assert DELEGATED_FEATURE_ID == "federation/delegated-workload:v1"
    assert FEDERATION_OWNERSHIP_SCHEME == "kamiwaza.validation/v1"
    assert plan.model_dump(by_alias=True)["schema"] == "kamiwaza.scenario-plan/v1"


@pytest.mark.parametrize("operation", ["prepare", "run"])
def test_related_serialized_old_plan_refused_before_clients_or_journal(
    related_provider, tmp_path: Path, operation: str
) -> None:
    provider, factory, plan = related_provider
    legacy = _serialized_legacy_plan(plan)
    before = legacy.model_dump_json(by_alias=True)
    runtime = _runtime(tmp_path)
    writer = RecordingFixtureStateWriter()
    with pytest.raises(ProviderContractError, match="provider revision mismatch"):
        if operation == "prepare":
            provider.prepare(legacy, runtime, writer)
        else:
            provider.run(legacy, runtime, None)
    factory.assert_not_called()
    assert writer.snapshots == []
    assert legacy.model_dump_json(by_alias=True) == before


def _signed_legacy_state(
    plan: ScenarioPlan, runtime: RuntimeContext, writer: RecordingFixtureStateWriter
) -> FixtureState:
    legacy = _serialized_legacy_plan(plan)
    store = FederationStateStore(
        writer, runtime_ownership_key(runtime), legacy.provider_revision
    )
    state = store.initial(legacy, runtime)
    selected = legacy.selected[0]
    realm = selected.redacted_parameters["realm"]
    state = store.record(
        state,
        MutationSpec(selected.target_id, "keycloak-realm", realm),
        {"realm": realm, "issuer": selected.redacted_parameters["issuer"]},
    )
    state = store.record(
        state,
        MutationSpec(selected.target_id, "keycloak-user", "old-user-id"),
        {"user:retired-fixture-reader-1": "old-user-id"},
    )
    state = FixtureState.model_validate_json(state.model_dump_json(by_alias=True))
    # Verify the complete old ownership digest and MAC before testing the cutover.
    validate_state(runtime, state, legacy.provider_revision)
    return state


@pytest.mark.parametrize("operation", ["run", "teardown"])
def test_related_signed_old_state_refused_before_clients_or_mutation(
    related_provider, tmp_path: Path, operation: str
) -> None:
    provider, factory, plan = related_provider
    runtime = _runtime(tmp_path)
    writer = RecordingFixtureStateWriter()
    state = _signed_legacy_state(plan, runtime, writer)
    before = state.model_dump_json(by_alias=True)
    snapshots = deepcopy(writer.snapshots)
    assert state.journal
    with pytest.raises(
        ProviderContractError, match="fixture state provider revision mismatch"
    ):
        if operation == "run":
            provider.run(plan, runtime, state)
        else:
            provider.teardown(runtime, state)
    factory.assert_not_called()
    assert state.model_dump_json(by_alias=True) == before
    assert writer.snapshots == snapshots
