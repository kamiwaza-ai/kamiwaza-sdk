"""A fresh gate package remains owned if later metadata or discovery fails."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kamiwaza_sdk.validation import federation_setup
from kamiwaza_sdk.validation.federation_cleanup import CleanupContext, cleanup_mutation
from kamiwaza_sdk.validation.federation_provider import FederationLifecycleProvider
from kamiwaza_sdk.validation.federation_state import (
    FederationStateStore,
    validate_state,
)
from kamiwaza_sdk.validation.inference_state import runtime_ownership_key
from kamiwaza_sdk.validation.provider import ProviderContractError
from kamiwaza_sdk.validation.testkit import RecordingFixtureStateWriter
from tests.contract.validation.test_federation_provider import _profile, _runtime
from tests.unit.test_access_tier_fixture_version import _client
from tests.unit.test_access_tier_fixture_version import gate_fixture as gate_fixture

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("failure", ["metadata", "discovery"])
def test_new_package_is_journaled_before_post_install_failure(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    directory, package = request.getfixturevalue("gate_fixture")
    monkeypatch.setenv("KAMIWAZA_SHARED_IDP_PUBLIC_URL", "https://idp.test")
    provider = FederationLifecycleProvider()
    plan = provider.resolve(_profile())
    runtime = _runtime(directory)
    writer = RecordingFixtureStateWriter()
    store = FederationStateStore(
        writer, runtime_ownership_key(runtime), plan.provider_revision
    )
    receiver = _client([], package)
    context = SimpleNamespace(
        initiator=Mock(),
        receiver=receiver,
        selected=plan.selected[0],
        state=store.initial(plan, runtime),
        store=store,
    )
    receiver.cluster = Mock()
    if failure == "metadata":
        package.hash_digest = "sha256:" + "1" * 64
    else:
        receiver.gates.discover.side_effect = RuntimeError("discovery failed")

    with pytest.raises((ProviderContractError, RuntimeError)):
        federation_setup._configure_edge(context)

    state = writer.snapshots[-1]
    validate_state(runtime, state, plan.provider_revision)
    assert len(state.journal) == 1
    mutation = state.journal[0]
    assert (mutation.resource_type, mutation.resource_id) == (
        "gate-package",
        "acme-gates",
    )
    receiver.gates.packages.uninstall.assert_not_called()
    cleanup = CleanupContext({}, receiver, Mock(), runtime)
    assert cleanup_mutation(mutation, cleanup).status == "removed"
    receiver.gates.packages.uninstall.assert_called_once_with("acme-gates")
