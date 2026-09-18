"""Identity administration and token fakes for federation lifecycle contracts."""

from __future__ import annotations

import base64
import json
from typing import Any

from kamiwaza_sdk.validation import RuntimeContext
from tests.contract.validation.federation_test_support import _NotFound


def _jwt(subject: str) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = (
        base64.urlsafe_b64encode(json.dumps({"sub": subject}).encode())
        .decode()
        .rstrip("=")
    )
    return f"{header}.{payload}.signature"


class _Admin:
    def __init__(self) -> None:
        self.deleted_users: list[str] = []
        self.deleted_clients: list[str] = []
        self.deleted_realms: list[str] = []
        self.token_client_ids: list[str] = []

    def create_owned_realm(self, realm: str, owner_nonce: str) -> dict[str, Any]:
        del owner_nonce
        return {"realm": realm, "created": True}

    def delete_owned_realm(self, realm: str, owner_nonce: str) -> bool:
        del owner_nonce
        if realm in self.deleted_realms:
            raise _NotFound("realm is already absent")
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
        if user_id in self.deleted_users:
            raise _NotFound("user is already absent")
        self.deleted_users.append(user_id)
        return True

    def delete_client(self, realm: str, client_uuid: str) -> bool:
        del realm
        if client_uuid in self.deleted_clients:
            raise _NotFound("client is already absent")
        self.deleted_clients.append(client_uuid)
        return True

    def ropc_token(
        self, realm: str, client_id: str, username: str, password: str
    ) -> str:
        del realm, password
        self.token_client_ids.append(client_id)
        return _jwt(username)


class _AdminFactory:
    def __init__(self, admin: _Admin) -> None:
        self.admin = admin

    def __call__(self, runtime: RuntimeContext, cluster: Any) -> _Admin:
        del runtime, cluster
        return self.admin
