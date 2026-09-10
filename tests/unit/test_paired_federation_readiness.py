"""Positive paired cases must wait on reads before starting remote work."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.validation import federation_readiness
from tests.integration import test_federation_delegated_workload_live as delegated
from tests.integration import test_federation_shared_idp_gated_retrieval_live as shared

pytestmark = pytest.mark.unit


def _wiring(persona):
    return {
        "name": "receiver",
        "urn": "urn:fixture",
        "personas": {"U": {"client": persona, "authenticator": Mock()}},
    }


@pytest.mark.parametrize("case", ["retrieval", "job", "delegated"])
def test_unready_authority_prevents_remote_mutations(monkeypatch, case):
    denied = APIError("denied", status_code=403)
    listing = Mock(side_effect=denied)
    persona = Mock()
    persona.catalog.datasets.list = listing
    wiring = _wiring(persona)
    retrieve = Mock()
    monkeypatch.setattr(shared.mc, "mesh_retrieve_through_gate", retrieve)
    monkeypatch.setattr(delegated, "_delegated_package_config", lambda: ((), (), {}))
    with pytest.raises(APIError) as caught:
        if case == "retrieval":
            shared.test_required_mesh_retrieval_returns_exact_post_gate_rows(
                "U", wiring, SimpleNamespace(base_url="https://source/api")
            )
        elif case == "job":
            shared.test_required_mesh_job_reaches_receiver_and_returns_marker(wiring)
        else:
            delegated.test_shared_idp_delegated_job_installs_approved_package(
                SimpleNamespace(getfixturevalue=lambda _: wiring)
            )
    assert caught.value is denied
    listing.assert_called_once_with(target_cluster="receiver")
    persona.jobs.run.assert_not_called()
    retrieve.assert_not_called()


def test_paired_dataset_case_waits_for_projection_and_keeps_exact_assertion(
    monkeypatch,
):
    pending = APIError(
        "pending",
        status_code=503,
        response_data={"detail": "authorization_unavailable"},
    )
    persona = Mock()
    persona.catalog.datasets.list.side_effect = [
        pending,
        [SimpleNamespace(urn="urn:fixture")],
    ]
    monkeypatch.setattr(federation_readiness.time, "sleep", Mock())
    shared.test_required_mesh_dataset_list_returns_only_authorized_fixture(
        _wiring(persona)
    )
    assert persona.catalog.datasets.list.call_count == 2
    persona.catalog.datasets.list.return_value = [SimpleNamespace(urn="urn:unrelated")]
    persona.catalog.datasets.list.side_effect = None
    with pytest.raises(AssertionError):
        shared.test_required_mesh_dataset_list_returns_only_authorized_fixture(
            _wiring(persona)
        )
