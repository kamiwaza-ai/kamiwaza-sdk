from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from kamiwaza_sdk.seeding.federation.keycloak import (
    OWNED_REALM_ATTRIBUTE,
    KeycloakAdmin,
    KeycloakAdminError,
)
from tests.integration import _shared_idp_fixture as fixture


@dataclass
class _Response:
    status_code: int
    body: Any = None
    text: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        return self.body


def _client(
    monkeypatch: pytest.MonkeyPatch, responses: list[_Response | BaseException]
):
    calls: list[tuple[str, str, dict[str, Any]]] = []
    client = KeycloakAdmin("https://keycloak", admin_user="admin", admin_password="pw")

    def request(method: str, path: str, **kwargs: Any) -> _Response:
        calls.append((method, path, kwargs))
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(client, "_req", request)
    return client, calls


def test_create_owned_realm_refuses_existing_before_any_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls = _client(monkeypatch, [_Response(200, {"realm": "taken"})])

    with pytest.raises(KeycloakAdminError, match="pre-existing"):
        client.create_owned_realm("taken", "owner-a")

    assert [(method, path) for method, path, _ in calls] == [("GET", "/realms/taken")]


def test_create_owned_realm_stamps_exact_owner_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls = _client(monkeypatch, [_Response(404), _Response(201)])

    client.create_owned_realm("unique", "owner-a")

    assert calls[1] == (
        "POST",
        "/realms",
        {
            "json": {
                "realm": "unique",
                "enabled": True,
                "attributes": {OWNED_REALM_ATTRIBUTE: "owner-a"},
            }
        },
    )


@pytest.mark.parametrize("operation", ["create", "delete"])
def test_owned_realm_operations_reject_empty_nonce(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    client, calls = _client(monkeypatch, [])

    with pytest.raises(KeycloakAdminError, match="owner nonce"):
        getattr(client, f"{operation}_owned_realm")("unique", "")

    assert calls == []


def test_create_owned_realm_fails_on_non_not_found_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls = _client(monkeypatch, [_Response(503, text="unavailable")])

    with pytest.raises(KeycloakAdminError, match="preflight.*503"):
        client.create_owned_realm("unique", "owner-a")

    assert [(method, path) for method, path, _ in calls] == [("GET", "/realms/unique")]


def test_create_owned_realm_post_409_race_never_deletes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls = _client(
        monkeypatch,
        [
            _Response(404),
            _Response(409, text="race"),
            _Response(200, {"attributes": {OWNED_REALM_ATTRIBUTE: "owner-a"}}),
        ],
    )

    with pytest.raises(KeycloakAdminError, match="409"):
        client.create_owned_realm("unique", "owner-a")

    assert [method for method, _, _ in calls] == ["GET", "POST", "GET"]


def test_ambiguous_post_that_committed_owned_realm_is_reconciled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeout = TimeoutError("connection lost after commit")
    owned = _Response(200, {"attributes": {OWNED_REALM_ATTRIBUTE: "owner-a"}})
    client, calls = _client(
        monkeypatch,
        [_Response(404), timeout, owned, owned, _Response(204)],
    )

    with pytest.raises(TimeoutError, match="after commit"):
        client.create_owned_realm("unique", "owner-a")

    assert [method for method, _, _ in calls] == [
        "GET",
        "POST",
        "GET",
        "GET",
        "DELETE",
    ]


def test_ambiguous_post_refuses_competing_owner_without_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls = _client(
        monkeypatch,
        [
            _Response(404),
            TimeoutError("ambiguous"),
            _Response(
                200,
                {"attributes": {OWNED_REALM_ATTRIBUTE: "competing-owner"}},
            ),
        ],
    )

    with pytest.raises(KeycloakAdminError, match="different owner"):
        client.create_owned_realm("unique", "owner-a")

    assert [method for method, _, _ in calls] == ["GET", "POST", "GET"]


def test_delete_owned_realm_deletes_only_exact_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls = _client(
        monkeypatch,
        [
            _Response(200, {"attributes": {OWNED_REALM_ATTRIBUTE: "owner-a"}}),
            _Response(204),
        ],
    )

    assert client.delete_owned_realm("unique", "owner-a") is True
    assert [(method, path) for method, path, _ in calls] == [
        ("GET", "/realms/unique"),
        ("DELETE", "/realms/unique"),
    ]


def test_delete_owned_realm_refuses_unowned_without_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls = _client(
        monkeypatch,
        [_Response(200, {"attributes": {OWNED_REALM_ATTRIBUTE: "someone-else"}})],
    )

    with pytest.raises(KeycloakAdminError, match="unowned"):
        client.delete_owned_realm("unique", "owner-a")

    assert [(method, path) for method, path, _ in calls] == [("GET", "/realms/unique")]


def test_delete_owned_realm_absent_is_idempotent_for_ambiguous_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls = _client(monkeypatch, [_Response(404)])

    assert client.delete_owned_realm("unique", "owner-a") is False
    assert len(calls) == 1


def test_delete_owned_realm_propagates_delete_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls = _client(
        monkeypatch,
        [
            _Response(200, {"attributes": {OWNED_REALM_ATTRIBUTE: "owner-a"}}),
            _Response(500, text="delete failed"),
        ],
    )

    with pytest.raises(KeycloakAdminError, match="delete.*500"):
        client.delete_owned_realm("unique", "owner-a")

    assert [method for method, _, _ in calls] == ["GET", "DELETE"]


@pytest.mark.parametrize(
    ("delete_error", "expected_message"),
    [
        (None, "profile mutation failed"),
        (KeycloakAdminError("delete failed"), "rollback also failed"),
    ],
)
def test_partial_provision_rolls_back_owned_realm(
    monkeypatch: pytest.MonkeyPatch,
    delete_error: Exception | None,
    expected_message: str,
) -> None:
    events: list[tuple[str, str, str | None]] = []

    class _Admin:
        def create_owned_realm(self, realm: str, owner: str) -> None:
            events.append(("create", realm, owner))

        def set_unmanaged_attributes(self, realm: str) -> None:
            events.append(("profile", realm, None))
            raise RuntimeError("profile mutation failed")

        def delete_owned_realm(self, realm: str, owner: str) -> bool:
            events.append(("delete", realm, owner))
            if delete_error:
                raise delete_error
            return True

    monkeypatch.setattr(
        "kamiwaza_sdk.seeding.federation.keycloak.KeycloakAdmin",
        lambda *args, **kwargs: _Admin(),
    )

    with pytest.raises(RuntimeError, match=expected_message):
        fixture.provision(
            "http://keycloak",
            "admin-pw",
            "persona-pw",
            fixture.OwnedRealm("unique", "owner-a"),
        )

    assert events == [
        ("create", "unique", "owner-a"),
        ("profile", "unique", None),
        ("delete", "unique", "owner-a"),
    ]


def test_provision_creates_clearance_and_unonboarded_personas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    users: list[tuple[str, dict[str, str]]] = []

    class _Admin:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def create_owned_realm(self, _realm: str, _owner: str) -> None:
            pass

        def set_unmanaged_attributes(self, _realm: str) -> None:
            pass

        def ensure_ropc_client(self, _realm: str, _client: str) -> dict[str, str]:
            return {"id": "client-id"}

        def ensure_attribute_mapper(
            self, _realm: str, _client: str, *, attribute: str
        ) -> None:
            assert attribute == "clearance"

        def ensure_user(
            self,
            _realm: str,
            username: str,
            *,
            password: str,
            attributes: dict[str, str],
        ) -> None:
            assert password == "persona-pw"
            users.append((username, attributes))

        def issuer_url(self, realm: str) -> str:
            return f"https://keycloak/realms/{realm}"

    monkeypatch.setattr(
        "kamiwaza_sdk.seeding.federation.keycloak.KeycloakAdmin",
        _Admin,
    )

    fixture.provision(
        "https://keycloak",
        "admin-pw",
        "persona-pw",
        fixture.OwnedRealm("unique", "owner-a"),
    )

    assert users == [
        ("fed-clr-u", {"clearance": "U"}),
        ("fed-clr-s", {"clearance": "S"}),
        ("fed-clr-ts", {"clearance": "TS"}),
        ("fed-clr-unonboarded", {"clearance": "U"}),
    ]
