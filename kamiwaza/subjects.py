"""T5.5 / ENG-4741 — kamiwaza.subjects module.

Customer-facing surface for AuthzSubjects per design §4.2.11:

    kz.subjects.upsert(username, attributes=..., password=...) -> Subject
    kz.subjects.get(username)                                  -> Subject
    kz.subjects.delete(username, cascade_grants=False)         -> None
    kz.subjects.grants(username).create(...)                   -> Grant
    kz.subjects.grants(username).list()                        -> list[Grant]
    kz.subjects.grants(username).delete(...)                   -> None

Server-side correlates (§4.2.6):
    PUT    /api/authz/subjects/{id_or_username}
    GET    /api/authz/subjects/{id_or_username}
    DELETE /api/authz/subjects/{id_or_username}?cascade=grants
    POST   /api/authz/subjects/{id_or_username}/grants
    GET    /api/authz/subjects/{id_or_username}/grants
    DELETE /api/authz/subjects/{id_or_username}/grants

v0.3.5 OQ-11: PUT-only on upsert — no POST surface.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from kamiwaza.models import Grant, Subject


class SubjectsAPI:
    """Subject lifecycle on the local cluster.

    The demo author's first reach for ``setup.py`` — collapses the
    two-phase Keycloak admin recipe into a single typed call.
    """

    def __init__(self, client: Any) -> None:
        # ``Any`` avoids a runtime cycle (client lazy-imports this module).
        self._client = client

    def upsert(
        self,
        username: str,
        *,
        attributes: Dict[str, Any],
        password: Optional[str] = None,
    ) -> Subject:
        """Idempotent upsert (PUT) per §4.2.6 / v0.3.5 OQ-11.

        Args:
            username: KC username (or UUID — the server treats both as the
                same path segment).
            attributes: Attribute dict; list values become multivalued KC
                attributes (T3.3 server-side inference).
            password: Optional initial password. Omit to leave credentials
                untouched on existing subjects.

        Returns:
            Subject — full response with KC id + timestamps + grants.
        """
        body: Dict[str, Any] = {"attributes": dict(attributes)}
        if password is not None:
            body["password"] = password
        response = self._client._request(
            "PUT", f"/api/authz/subjects/{username}", json=body
        )
        return Subject.model_validate(response)

    def get(self, username: str) -> Subject:
        """Read the Subject by KC UUID or username.

        Raises:
            KamiwazaError: 404 subject_not_found when absent.
        """
        response = self._client._request("GET", f"/api/authz/subjects/{username}")
        return Subject.model_validate(response)

    def delete(self, username: str, *, cascade_grants: bool = False) -> None:
        """Delete the Subject. ``cascade_grants=True`` also removes the
        subject's ReBAC tuples (T3.6 server-side cascade)."""
        path = f"/api/authz/subjects/{username}"
        if cascade_grants:
            path = f"{path}?cascade=grants"
        self._client._request("DELETE", path)

    def grants(self, username: str) -> "SubjectGrantsAPI":
        """Grants sub-resource scoped to a subject.

        Returns a handle for the subject's ReBAC tuples:

            kz.subjects.grants("alice").create(
                object_namespace="cluster",
                object_id="...",
                relation="viewer",
            )
        """
        return SubjectGrantsAPI(client=self._client, username=username)


class SubjectGrantsAPI:
    """Subject-scoped grants surface — wraps the relationship_store
    delegation on the server (T3.6)."""

    def __init__(self, client: Any, username: str) -> None:
        self._client = client
        self._username = username

    def create(
        self,
        *,
        object_namespace: str,
        object_id: str,
        relation: str,
    ) -> Grant:
        """Bind a ReBAC tuple ``(subject, object, relation)``."""
        body = {
            "object_namespace": object_namespace,
            "object_id": object_id,
            "relation": relation,
        }
        response = self._client._request(
            "POST",
            f"/api/authz/subjects/{self._username}/grants",
            json=body,
        )
        return Grant.model_validate(response)

    def list(self) -> List[Grant]:
        """Return all grants bound to this subject."""
        response = self._client._request(
            "GET", f"/api/authz/subjects/{self._username}/grants"
        )
        items = response if isinstance(response, list) else []
        return [Grant.model_validate(item) for item in items]

    def delete(
        self,
        *,
        object_namespace: str,
        object_id: str,
        relation: str,
    ) -> None:
        """Remove the tuple ``(subject, object, relation)``.

        Tuple key in the body, not the path — relation values can carry
        characters that don't path-encode cleanly.
        """
        body = {
            "object_namespace": object_namespace,
            "object_id": object_id,
            "relation": relation,
        }
        self._client._request(
            "DELETE",
            f"/api/authz/subjects/{self._username}/grants",
            json=body,
        )
