"""T5.3 / ENG-4679 — kamiwaza.federations module.

Customer-facing surface for federation pairing + brokered user
management per design §4.2.11. Exposed on the client as
``kz.federations`` (lazy-loaded — see kamiwaza.client.Kamiwaza.federations).

API shape:

    kz.federations.pair(name, role, remote_url, ...)  -> Federation
    kz.federations.list()                              -> list[Federation]   (T5.x)
    kz.federations.get(name)                           -> Federation         (T5.x)
    kz.federations[name]                               -> FederationProxy
    kz.federations[name].users.add(external_id, ...)   -> BrokeredUser
    kz.federations[name].users.list()                  -> list[BrokeredUser] (T5.x)
    kz.federations[name].users.revoke(external_id)     -> None               (T4.16-revocation)

The skeleton ships pair + indexed access + users.add — the load-bearing
slice for T4.16-skeleton's integration test. List / get / revoke land in
subsequent T5.x cycles when WS-M2 active-revocation work picks them up.

Server-side correlate: ``kamiwaza.cluster.api.cluster_router``
(/federations, /federations/{id}/pair) and ``FederationUsersAPI``
(/federations/{id}/users — T4.5 / ENG-4667).
"""

from __future__ import annotations

from typing import Any, List, Optional

from kamiwaza.models import BrokeredUser, Federation


class FederationsAPI:
    """Top-level federation operations on the local cluster."""

    def __init__(self, client: Any) -> None:
        # client is a kamiwaza.client.Kamiwaza instance. Typed as Any to
        # avoid a runtime cycle (client imports federations lazily, and
        # federations would import client for type only — vulture flags
        # TYPE_CHECKING-only imports as unused).
        self._client = client

    def pair(
        self,
        name: str,
        role: str,
        remote_url: str,
        *,
        remote_admin_token: Optional[str] = None,
        remote_ips: Optional[List[Any]] = None,
    ) -> Federation:
        """Initiate or accept a federation pairing.

        Two-step flow per design §4.2.1:
          1. POST /api/cluster/federations to create the federation row in
             WAITING/PAIRING state.
          2. POST /api/cluster/federations/{id}/pair to drive the actual
             handshake (PSK barrier, callback-host exchange, IdP
             registration).

        Args:
            name: Human-readable federation name (also remote_cluster_name).
            role: "initiator" or "responder".
            remote_url: HTTPS URL of the remote cluster's API.
            remote_admin_token: PAT/admin token on the remote cluster
                (initiator-only; required to drive the pair handshake).
            remote_ips: Override remote cluster routable IPs; defaults to
                resolving from remote_url.

        Returns:
            Federation record after pairing completes.
        """
        create_body: dict[str, Any] = {
            "remote_cluster_name": name,
            "role": role,
            "remote_url": remote_url,
        }
        if remote_admin_token is not None:
            create_body["remote_admin_token"] = remote_admin_token
        if remote_ips is not None:
            create_body["remote_ips"] = remote_ips

        created = self._client._request(
            "POST", "/api/cluster/federations", json=create_body
        )
        federation_id = created["id"]

        paired = self._client._request(
            "POST",
            f"/api/cluster/federations/{federation_id}/pair",
        )
        return Federation.model_validate(paired)

    def __getitem__(self, name: str) -> "FederationProxy":
        """``kz.federations["ORION"]`` — proxy for sub-resource access."""
        return FederationProxy(client=self._client, name=name)

    def _resolve_id(self, name: str) -> str:
        """Resolve a federation by name to its UUID id.

        Used by FederationProxy for lazy id resolution; one round-trip
        cached on the proxy so users.add / users.revoke don't refetch.
        """
        body = self._client._request("GET", "/api/cluster/federations")
        items: List[Any] = []
        if isinstance(body, dict):
            raw = body.get("items")
            if isinstance(raw, list):
                items = raw
        for item in items:
            if isinstance(item, dict) and item.get("remote_cluster_name") == name:
                return str(item["id"])
        # Skeleton: surface as a generic KamiwazaError. T5.x adds
        # FederationNotFoundError when WS-M2 cluster.diagnose lands.
        from kamiwaza.exceptions import KamiwazaError

        raise KamiwazaError(
            f"No federation named {name!r} on this cluster. "
            "List federations with kz.federations.list()."
        )


class FederationProxy:
    """Sub-resource accessor for a single named federation.

    Lazily resolves the federation's id on first sub-resource use so that
    indexed access (``kz.federations["ORION"]``) doesn't cost a round-trip
    by itself. The id is cached on the proxy after first resolution.
    """

    def __init__(self, *, client: Any, name: str) -> None:
        # client is a kamiwaza.client.Kamiwaza instance — see FederationsAPI.
        self._client = client
        self.name = name
        self._cached_id: Optional[str] = None

    @property
    def users(self) -> "FederationUsersAPI":
        return FederationUsersAPI(proxy=self)

    def _id(self) -> str:
        cached = self._cached_id
        if cached is None:
            cached = self._client.federations._resolve_id(self.name)
            self._cached_id = cached
        return cached


class FederationUsersAPI:
    """Brokered-user management on a single federation."""

    def __init__(self, *, proxy: FederationProxy) -> None:
        self._proxy = proxy
        self._client = proxy._client

    def add(
        self,
        external_id: str,
        *,
        initial_tuples: Optional[List[Any]] = None,
    ) -> BrokeredUser:
        """Allowlist a brokered user on this federation (FR-51 / FR-80).

        The user is added to the receiver's allowlist; on first mesh
        request, the receiver's BrokeringService auto-provisions the
        local KC user record and seeds the supplied initial_tuples as
        ReBAC grants.

        Args:
            external_id: ``"<username>@<peer-cluster-uuid>"`` format.
            initial_tuples: ReBAC tuples to seed at provisioning. Each
                tuple is a dict with ``subject`` / ``relation`` /
                ``object`` keys.

        Returns:
            Newly-created BrokeredUser record (auto_provisioned=False
            until the user makes their first mesh request).
        """
        body: dict[str, Any] = {"external_id": external_id}
        if initial_tuples is not None:
            body["initial_tuples"] = initial_tuples

        result = self._client._request(
            "POST",
            f"/api/cluster/federations/{self._proxy._id()}/users",
            json=body,
        )
        return BrokeredUser.model_validate(result)
