"""Fail-closed lifecycle for uniquely owned Keycloak fixture realms."""

from __future__ import annotations

from typing import Any, Callable, Dict, Generic, Protocol, TypeVar
from urllib.parse import quote

OWNED_REALM_ATTRIBUTE = "kajiya.fixture.owner_nonce"


class _Response(Protocol):
    status_code: int
    text: str


_ResponseT = TypeVar("_ResponseT", bound=_Response)


class OwnedRealmLifecycle(Generic[_ResponseT]):
    """Create/reconcile/delete a realm through injected admin HTTP primitives."""

    def __init__(
        self,
        request: Callable[..., _ResponseT],
        parse_json: Callable[[_ResponseT, str], Any],
        error_type: type[Exception],
    ) -> None:
        self._request = request
        self._parse_json = parse_json
        self._error_type = error_type

    def create(self, realm: str, owner_nonce: str) -> Dict[str, Any]:
        """Create only a new realm, reconciling ambiguous POST outcomes."""
        self._require_nonce(owner_nonce, "requires")
        got = self._request("GET", self._path(realm))
        if got.status_code == 200:
            raise self._error(f"refusing to mutate pre-existing realm {realm!r}")
        if got.status_code != 404:
            raise self._error(
                f"preflight realm {realm!r} failed ({got.status_code}): {got.text[:200]}"
            )
        try:
            created = self._request(
                "POST",
                "/realms",
                json={
                    "realm": realm,
                    "enabled": True,
                    "attributes": {OWNED_REALM_ATTRIBUTE: owner_nonce},
                },
            )
        except Exception as post_exception:
            self._finish_failed_create(
                realm, owner_nonce, post_exception, delete_if_owned=True
            )
            raise
        if created.status_code != 201:
            status_error = self._error(
                f"create new owned realm {realm!r} failed ({created.status_code}): "
                f"{created.text[:200]}"
            )
            self._finish_failed_create(
                realm,
                owner_nonce,
                status_error,
                delete_if_owned=created.status_code != 409,
            )
            raise status_error
        return {"realm": realm, "created": True, "owner_nonce": owner_nonce}

    def delete(self, realm: str, owner_nonce: str) -> bool:
        """Delete only a realm carrying the exact nonce; absent is success."""
        self._require_nonce(owner_nonce, "teardown requires")
        path = self._path(realm)
        got = self._request("GET", path)
        if got.status_code == 404:
            return False
        realm_rep = self._parse_json(got, f"read realm {realm!r}") or {}
        if self._owner(realm_rep) != owner_nonce:
            raise self._error(f"refusing to delete unowned realm {realm!r}")
        deleted = self._request("DELETE", path)
        if deleted.status_code not in (204, 404):
            raise self._error(
                f"delete owned realm {realm!r} failed ({deleted.status_code}): "
                f"{deleted.text[:200]}"
            )
        return deleted.status_code == 204

    def _finish_failed_create(
        self,
        realm: str,
        owner_nonce: str,
        create_error: Exception,
        *,
        delete_if_owned: bool,
    ) -> None:
        outcome = self._reconcile(realm, owner_nonce, delete_if_owned=delete_if_owned)
        if outcome == "different-owner":
            raise self._error(
                f"create outcome for realm {realm!r} failed or is ambiguous and "
                "the observed realm has a different owner; refusing cleanup"
            ) from create_error

    def _reconcile(self, realm: str, owner_nonce: str, *, delete_if_owned: bool) -> str:
        try:
            got = self._request("GET", self._path(realm))
            if got.status_code == 404:
                return "absent"
            realm_rep = self._parse_json(got, f"reconcile realm {realm!r}") or {}
            if self._owner(realm_rep) != owner_nonce:
                return "different-owner"
            if delete_if_owned:
                self.delete(realm, owner_nonce)
                return "owned-deleted"
            return "owned-not-deleted"
        except Exception as cleanup_error:
            raise self._error(
                f"create realm {realm!r} failed and ownership reconciliation also failed"
            ) from cleanup_error

    def _require_nonce(self, owner_nonce: str, action: str) -> None:
        if not owner_nonce:
            raise self._error(f"owned realm {action} a non-empty owner nonce")

    def _error(self, message: str) -> Exception:
        return self._error_type(message)

    @staticmethod
    def _path(realm: str) -> str:
        return f"/realms/{quote(realm)}"

    @staticmethod
    def _owner(realm_rep: Dict[str, Any]) -> Any:
        return (realm_rep.get("attributes") or {}).get(OWNED_REALM_ATTRIBUTE)
