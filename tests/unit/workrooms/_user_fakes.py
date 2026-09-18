"""Deployment-free doubles for the ENG-12325 disposable workroom user.

The admin-side account calls run against an in-memory user store, and the
user-side client answers the login, whoami and logout the helper makes. Faults
are injected per call so a test states only the failure it is pinning.

The credential-rendering probes run pytest in a subprocess and import this
module, so everything they reach has to live here rather than in a test module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from kamiwaza_sdk.exceptions import APIError, NotFoundError
from kamiwaza_sdk.schemas.auth import LocalUserResponse, TokenResponse, UserInfo
from tests.integration import _workroom_disposable_user as disposable

REAL_ANNOUNCE_ACCOUNT = disposable._announce_account


SECRET_MARKER = "sekret42"

REPO_ROOT = Path(__file__).resolve().parents[3]


def sdk_error() -> APIError:
    return APIError(f"request failed: {SECRET_MARKER}", status_code=400)


def token_validation_error() -> Exception:
    """The error the SDK raises when a login response lacks ``expires_in``."""
    try:
        TokenResponse.model_validate(
            {"access_token": SECRET_MARKER, "refresh_token": SECRET_MARKER}
        )
    except Exception as exc:  # noqa: BLE001 - capture pydantic's real error
        return exc
    raise AssertionError("the malformed token response validated")


class AdminAuth:
    """Admin-side account calls against an in-memory user store."""

    def __init__(
        self,
        *,
        create_error: Exception | None = None,
        store_before_create_error: bool = False,
        delete_applies: bool = True,
        delete_error: Exception | None = None,
    ) -> None:
        self.create_error = create_error
        self.store_before_create_error = store_before_create_error
        self.delete_applies = delete_applies
        self.delete_error = delete_error
        self.users: dict[UUID, LocalUserResponse] = {}
        self.requested_roles: list[object] = []
        self.deleted: list[UUID] = []
        self.readbacks: list[UUID] = []
        self.listings = 0

    def create_local_user(self, payload):
        self.requested_roles.append(payload.roles)
        if self.create_error is not None and not self.store_before_create_error:
            raise self.create_error
        created = LocalUserResponse(
            id=uuid4(),
            username=payload.username,
            active=True,
            deleted=False,
            is_external=False,
        )
        self.users[created.id] = created
        if self.create_error is not None:
            raise self.create_error
        return created

    def list_users(self):
        self.listings += 1
        return [user for user in self.users.values() if not user.deleted]

    def get_user(self, user_id):
        self.readbacks.append(user_id)
        user = self.users.get(user_id)
        if user is None or user.deleted:
            raise NotFoundError("User not found")
        return user

    def delete_user(self, user_id):
        self.deleted.append(user_id)
        if self.delete_error is not None:
            raise self.delete_error
        if self.delete_applies:
            self.users[user_id] = self.users[user_id].model_copy(
                update={"deleted": True}
            )
        return self.users[user_id]


class Admin:
    def __init__(self, auth: AdminAuth) -> None:
        self.auth = auth


@dataclass(frozen=True)
class Grant:
    """What a successful password login answers with.

    Bundled because the failures a test injects and the grant it hands back are
    two concerns, and most tests vary only the failures. Keeping them apart also
    holds the client's constructor inside the argument cap.
    """

    access_token: str = "access-token"
    refresh_token: str = "refresh"
    expires_in: int = 300
    roles: tuple[str, ...] = ("offline_access", "user")


class UserClient:
    def __init__(
        self,
        *,
        # BaseException: the helper must handle a stop signal from these calls
        # as well as a failure, and a test raises each.
        login_error: BaseException | None = None,
        whoami_error: BaseException | None = None,
        logout_error: BaseException | None = None,
        grant: Grant | None = None,
    ) -> None:
        grant = grant or Grant()
        self.roles = list(grant.roles)
        self.access_token = grant.access_token
        self.expires_in = grant.expires_in
        self.refresh_token = grant.refresh_token
        self.login_error = login_error
        self.whoami_error = whoami_error
        self.logout_error = logout_error
        self.logger_disabled_during_login: bool | None = None
        # A real client built without an api_key adopts KAMIWAZA_API_KEY from
        # the environment; the sentinel stands in for that ambient credential.
        self.authenticator = "ambient-credential"
        self.auth = self
        self.posted: list[str] = []
        self.bodies: list[dict] = []
        self.closed = 0

    def login_with_password(self, _username, _password):
        self.logger_disabled_during_login = logging.getLogger(
            disposable.SDK_CLIENT_LOGGER
        ).disabled
        if self.login_error is not None:
            raise self.login_error
        return TokenResponse(
            access_token=self.access_token,
            refresh_token=self.refresh_token,
            expires_in=self.expires_in,
        )

    def get_current_user(self):
        if self.whoami_error is not None:
            raise self.whoami_error
        return UserInfo(username="u", sub="subject-id", roles=self.roles)

    def post(self, path, **body):
        self.posted.append(path)
        self.bodies.append(body)
        if self.logout_error is not None:
            raise self.logout_error

    def close(self):
        self.closed += 1


class Config:
    OPTIONS = ("showlocals", "fulltrace", "usepdb", "trace")

    def __init__(self, **enabled: bool) -> None:
        self.options = {name: enabled.get(name, False) for name in self.OPTIONS}
        self.asked: list[str] = []

    def getoption(self, name, default=None):
        self.asked.append(name)
        return self.options.get(name, default)


def factory(client: UserClient):
    return lambda **_: client


def create(auth: AdminAuth, client: UserClient) -> disposable.WorkroomUser:
    return disposable.create_workroom_user(Admin(auth), factory(client), "http://x")


TRACE_ENV = {
    "http_trace": ("KAMIWAZA_HTTP_TRACE", "1"),
    "http_trace_file": ("KAMIWAZA_HTTP_TRACE_FILE", "/tmp/kamiwaza-trace.jsonl"),
}
