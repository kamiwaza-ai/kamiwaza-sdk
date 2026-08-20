"""Offline contracts for required shared-IDP persona provisioning."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

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


def test_persona_provisioning_onboards_only_clearance_personas(monkeypatch) -> None:
    sessions: dict[str, dict[str, Any]] = {}

    def programmatic_session(_base_url, _auth, username):
        persona = {
            "token": f"token-{username}",
            "client": SimpleNamespace(close=Mock()),
        }
        sessions[username] = persona
        return persona

    monkeypatch.setattr(edge, "_programmatic_persona_session", programmatic_session)
    monkeypatch.setattr(
        edge.mc,
        "jwt_sub",
        lambda token: f"sub-{token.removeprefix('token-')}",
    )
    receiver = SimpleNamespace(_request=Mock())

    personas = edge._provision_personas(
        Mock(),
        "https://fed-a.test/api",
        receiver,
        _identifiers(),
        _prerequisites(),
    )

    assert set(personas) == {"U", "S", "TS", "unonboarded"}
    assert set(sessions) == {
        "fed-clr-u",
        "fed-clr-s",
        "fed-clr-ts",
        "fed-clr-unonboarded",
    }
    assert receiver._request.call_count == 3
    onboarded = [call.kwargs["json"] for call in receiver._request.call_args_list]
    assert {row["external_id"] for row in onboarded} == {
        "sub-fed-clr-u@source-cluster",
        "sub-fed-clr-s@source-cluster",
        "sub-fed-clr-ts@source-cluster",
    }
    assert len(onboarded[0]["initial_tuples"]) == 2
    assert all(len(row["initial_tuples"]) == 1 for row in onboarded[1:])


def test_persona_provisioning_rejects_token_without_subject(monkeypatch) -> None:
    monkeypatch.setattr(
        edge,
        "_programmatic_persona_session",
        lambda *_args: {
            "token": "token-without-sub",
            "client": SimpleNamespace(close=Mock()),
        },
    )
    monkeypatch.setattr(edge.mc, "jwt_sub", lambda _token: "")
    receiver = SimpleNamespace(_request=Mock())

    with pytest.raises(AssertionError, match="subject"):
        edge._provision_personas(
            Mock(),
            "https://fed-a.test/api",
            receiver,
            _identifiers(),
            _prerequisites(),
        )

    receiver._request.assert_not_called()
