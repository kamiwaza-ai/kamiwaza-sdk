"""Provider implementation revisions guard replay without changing wire schemas."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from kamiwaza_sdk.validation.federation_provider import FederationLifecycleProvider
from kamiwaza_sdk.validation.federation_spec import FEDERATION_OWNERSHIP_SCHEME
from kamiwaza_sdk.validation.federation_state import FederationStateStore
from kamiwaza_sdk.validation.inference_state import runtime_ownership_key
from kamiwaza_sdk.validation.provider import ProviderContractError
from kamiwaza_sdk.validation.testkit import RecordingFixtureStateWriter
from tests.contract.validation.test_federation_provider import _profile, _runtime

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
