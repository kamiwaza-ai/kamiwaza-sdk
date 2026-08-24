"""Offline contracts for required shared-IDP persona provisioning."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, call

import pytest

from tests.integration import test_federation_shared_idp_gated_retrieval_live as edge

pytestmark = pytest.mark.unit


def _prerequisites() -> SimpleNamespace:
    return SimpleNamespace(
        persona_auth={"password": "secret"},
        shared={"shared_issuer_url": "https://idp.test/realms/shared"},
    )


def _identifiers() -> tuple[str, str, str]:
    return "receiver-fed", "source-cluster", "urn:dataset:known"


def _provisioning(cleanup: Any, receiver: Any) -> edge._PersonaProvisioning:
    federation_id, source_cluster_id, dataset_urn = _identifiers()
    prerequisites = _prerequisites()
    return edge._PersonaProvisioning(
        cleanup=cleanup,
        initiator_base_url="https://fed-a.test/api",
        receiver=receiver,
        federation_id=federation_id,
        source_cluster_id=source_cluster_id,
        dataset_urn=dataset_urn,
        auth={
            **prerequisites.persona_auth,
            "issuer": prerequisites.shared["shared_issuer_url"],
        },
    )


def _programmatic_session_recorder(
    sessions: dict[str, dict[str, Any]],
):
    def open_session(_base_url, _auth, username):
        persona = {
            "token": f"token-{username}",
            "client": SimpleNamespace(close=Mock()),
        }
        sessions[username] = persona
        return persona

    return open_session


def _assert_persona_inventory(
    personas: dict[str, dict[str, Any]],
    sessions: dict[str, dict[str, Any]],
) -> None:
    assert set(personas) == {
        "U",
        "S",
        "TS",
        "unonboarded",
        "missing-canonical",
        "legacy-only",
        "canonical-nondefault",
    }
    assert set(sessions) == {
        "fed-clr-u",
        "fed-clr-s",
        "fed-clr-ts",
        "fed-clr-unonboarded",
        "fed-tenant-missing",
        "fed-tenant-legacy-only",
        "fed-tenant-nondefault",
    }


def _assert_onboarding_contract(receiver: Any) -> None:
    assert receiver._request.call_count == 6
    onboarded = [request.kwargs["json"] for request in receiver._request.call_args_list]
    tuples_by_external_id = {
        row["external_id"]: row["initial_tuples"] for row in onboarded
    }
    assert {
        external_id: len(tuples)
        for external_id, tuples in tuples_by_external_id.items()
    } == {
        "sub-fed-clr-u@source-cluster": 2,
        "sub-fed-clr-s@source-cluster": 1,
        "sub-fed-clr-ts@source-cluster": 1,
        "sub-fed-tenant-missing@source-cluster": 0,
        "sub-fed-tenant-legacy-only@source-cluster": 0,
        "sub-fed-tenant-nondefault@source-cluster": 0,
    }


def _assert_cleanup_contract(cleanup: Any) -> None:
    cleanup_callbacks = cleanup.callback.call_args_list
    assert len(cleanup_callbacks) == 13
    cleanup_functions = [callback.args[0] for callback in cleanup_callbacks]
    assert cleanup_functions.count(edge._cleanup_brokered_persona) == 6


def _assert_claim_contract(
    assert_default_claim: Mock,
    assert_tenant_claim_shape: Mock,
) -> None:
    assert assert_default_claim.call_count == 4
    assert assert_tenant_claim_shape.call_args_list == [
        call("token-fed-tenant-missing", {}, context="missing-canonical"),
        call(
            "token-fed-tenant-legacy-only",
            {"tenant": "__default__"},
            context="legacy-only",
        ),
        call(
            "token-fed-tenant-nondefault",
            {"tenant_id": "tenant-a"},
            context="canonical-nondefault",
        ),
    ]


def test_persona_token_contract_accepts_explicit_default_tenant(monkeypatch) -> None:
    monkeypatch.setattr(
        edge,
        "decode_jwt_payload",
        lambda _token: {"tenant_id": "__default__"},
    )

    edge._assert_default_tenant_claim("opaque-token")


@pytest.mark.parametrize(
    "claims",
    [
        {},
        {"tenant": "__default__"},
        {"tenant_id": "another-tenant"},
        {"tenant_id": ""},
    ],
    ids=["missing", "legacy-only", "non-default", "blank"],
)
def test_persona_token_contract_rejects_noncanonical_tenant_claims(
    monkeypatch, claims
) -> None:
    monkeypatch.setattr(
        edge,
        "decode_jwt_payload",
        lambda _token: claims,
    )
    with pytest.raises(AssertionError) as exc_info:
        edge._assert_default_tenant_claim("opaque-token")
    assert str(exc_info.value) == (
        "shared-IDP access token must carry tenant_id=__default__"
    )


@pytest.mark.parametrize(
    ("case_id", "claims", "expected"),
    [
        pytest.param("missing-canonical", {}, {}, id="missing-canonical"),
        pytest.param(
            "legacy-only",
            {"tenant": "__default__"},
            {"tenant": "__default__"},
            id="legacy-only",
        ),
        pytest.param(
            "canonical-nondefault",
            {"tenant_id": "tenant-a"},
            {"tenant_id": "tenant-a"},
            id="canonical-nondefault",
        ),
    ],
)
def test_tenant_negative_token_contract_accepts_exact_claim_shape(
    monkeypatch, case_id, claims, expected
) -> None:
    monkeypatch.setattr(edge, "decode_jwt_payload", lambda _token: claims)

    edge._assert_tenant_claim_shape(
        "opaque-token",
        expected,
        context=case_id,
    )


def test_tenant_negative_token_contract_does_not_disclose_claims(
    monkeypatch,
) -> None:
    secret_claim = "must-not-appear"
    monkeypatch.setattr(
        edge,
        "decode_jwt_payload",
        lambda _token: {"tenant_id": secret_claim},
    )

    with pytest.raises(AssertionError) as exc_info:
        edge._assert_tenant_claim_shape(
            "opaque-token",
            {},
            context="missing-canonical",
        )

    assert str(exc_info.value) == (
        "shared-IDP access token has unexpected tenant claim shape for "
        "missing-canonical"
    )
    assert secret_claim not in str(exc_info.value)


def test_persona_provisioning_onboards_default_and_tenant_negative_personas(
    monkeypatch,
) -> None:
    sessions: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(
        edge,
        "_programmatic_persona_session",
        _programmatic_session_recorder(sessions),
    )
    assert_default_claim = Mock()
    assert_tenant_claim_shape = Mock()
    monkeypatch.setattr(edge, "_assert_default_tenant_claim", assert_default_claim)
    monkeypatch.setattr(
        edge,
        "_assert_tenant_claim_shape",
        assert_tenant_claim_shape,
    )
    monkeypatch.setattr(
        edge.mc,
        "jwt_sub",
        lambda token: f"sub-{token.removeprefix('token-')}",
    )
    receiver = SimpleNamespace(_request=Mock())
    cleanup = Mock()

    personas = edge._provision_personas(_provisioning(cleanup, receiver))

    _assert_persona_inventory(personas, sessions)
    _assert_onboarding_contract(receiver)
    _assert_cleanup_contract(cleanup)
    _assert_claim_contract(assert_default_claim, assert_tenant_claim_shape)


def test_persona_provisioning_rejects_token_without_subject(monkeypatch) -> None:
    monkeypatch.setattr(
        edge,
        "_programmatic_persona_session",
        lambda *_args: {
            "token": "token-without-sub",
            "client": SimpleNamespace(close=Mock()),
        },
    )
    monkeypatch.setattr(edge, "_assert_default_tenant_claim", Mock())
    monkeypatch.setattr(edge, "_assert_tenant_claim_shape", Mock())
    monkeypatch.setattr(edge.mc, "jwt_sub", lambda _token: "")
    receiver = SimpleNamespace(_request=Mock())

    with pytest.raises(AssertionError, match="subject"):
        edge._provision_personas(_provisioning(Mock(), receiver))

    receiver._request.assert_not_called()
