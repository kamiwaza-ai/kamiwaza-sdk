"""T7.5 / ENG-5039 — Federation pairing + brokered-user management.

WS-M3.2 service migration. Brings the customer-facing federation surface
from ``kamiwaza/federations.py`` (M1+ skeleton) into the canonical
``kamiwaza_sdk.services`` namespace per design v0.3.7 §4.2.11.

API shape (accessed via ``client.federations``):

    client.federations.pair(name, role, remote_url=..., ...)  -> Federation
    client.federations[name]                                  -> FederationProxy
    client.federations[name].probe()                          -> ClusterCapabilities
    client.federations[name].users.add(external_id, ...)      -> BrokeredUser

ENG-5016 fix landed at migration time (per design §6.2 WS-M3.2 T7.5):

- ``pair()`` accepts ``preshared_key`` — auto-mints a UUID4 (Mode A
  default) when not supplied; caller-supplied values pass through verbatim
  (Modes B/C).
- ``pair()`` accepts ``callback_hostname`` — forwarded to the server's
  ``CreateClusterFederation`` body when supplied. When None, the server's
  callback-host auto-exchange (FR-37) runs.
- ``pair()`` drops the bogus ``remote_url`` server-body field — the
  server's ``CreateClusterFederation`` Pydantic schema doesn't accept it.
  The kwarg is still accepted for backward-compat with the M1+ call shape
  (setup.py uses it); the SDK derives ``remote_ips`` from the URL host
  when callers don't supply them explicitly.

Per OQ-17, three PSK trust modes:

- **Mode A (default, auto-mint UUID4):** suitable for single-operator
  setups where the same operator has admin on both clusters. PSK is
  intent confirmation; the admin token on each cluster is the primary
  auth gate.
- **Mode B (caller-supplied):** caller provides via env/config/CLI.
  Same threat model as A, but caller controls the value.
- **Mode C (cross-org out-of-band):** caller receives PSK via signed
  email / secrets manager / paper. Suitable for federation pairings
  where the operator on Cluster A doesn't have admin on Cluster B —
  the PSK is the actual auth gate at the unauthenticated
  ``/pair_federation`` cluster-trust endpoint.
"""

from __future__ import annotations

import uuid
from typing import Any, List, Optional
from urllib.parse import urlparse

from ..schemas.federation import BrokeredUser, ClusterCapabilities, Federation
from .base_service import BaseService


class FederationsAPI(BaseService):
    """Top-level federation operations on the local cluster."""

    def pair(
        self,
        name: str,
        role: str,
        remote_url: Optional[str] = None,
        *,
        remote_ips: Optional[List[Any]] = None,
        preshared_key: Optional[str] = None,
        callback_hostname: Optional[str] = None,
        remote_admin_token: Optional[str] = None,
        local_kc_issuer_url: Optional[str] = None,
        local_kc_jwks_url: Optional[str] = None,
        local_broker_client_id: Optional[str] = None,
        local_broker_client_secret: Optional[str] = None,
    ) -> Federation:
        """Initiate or accept a federation pairing.

        Two-step flow per design §4.2.1:
          1. ``POST /api/cluster/federations`` creates the federation row
             in WAITING (receiver) or PAIRING (initiator) state.
          2. ``POST /api/cluster/federations/{id}/pair`` drives the
             actual handshake (PSK barrier, callback-host exchange,
             IdP registration).

        Args:
            name: Human-readable federation name (also remote_cluster_name).
            role: ``"initiator"`` or ``"receiver"``.
            remote_url: HTTPS URL of the remote cluster. Used to derive
                ``remote_ips`` when callers don't supply them explicitly.
                **Not sent to the server** — the server's
                ``CreateClusterFederation`` schema accepts ``remote_ips``
                only.
            remote_ips: Override remote-cluster routable IPs. Required by
                the server's @root_validator for initiator role; the SDK
                derives a single-entry list from ``remote_url`` when not
                supplied (Mode B+ callers can override).
            preshared_key: Three trust modes per design §4.2.11 OQ-17:

                - **Mode A** (default, ``preshared_key=None``): SDK mints
                  a UUID4. Suitable for single-operator setups where the
                  same admin operates both clusters; the PSK is intent
                  confirmation, the admin token on each cluster is the
                  primary auth gate.
                - **Mode B** (caller-supplied via env/config/CLI):
                  caller controls the value but the threat model is
                  unchanged from A.
                - **Mode C** (cross-org out-of-band exchange): caller
                  receives PSK via signed email / secrets manager /
                  paper from a counterparty on the other cluster. The
                  PSK becomes the actual auth gate at the unauthenticated
                  ``/pair_federation`` cluster-trust endpoint; out-of-
                  band channel integrity matters here.

                The same value must be entered on both paired clusters;
                the caller is responsible for that in B/C.
            callback_hostname: Optional hostname/IP the remote cluster
                should use for callbacks during /pair. When None,
                server-side auto-detection runs (FR-37).
            remote_admin_token: PAT/admin token on the remote cluster
                (initiator-only convenience field; the server uses it to
                drive the /pair handshake from the initiator side).
            local_kc_issuer_url: ENG-5822 — optional per-pair Keycloak
                issuer URL for this cluster's brokering identity
                (e.g. ``https://kamiwaza.test/realms/kamiwaza``). When
                supplied, persisted onto the federation row and used by
                the pair handshake instead of the cluster's
                ``KAMIWAZA_KC_ISSUER_URL`` process-env default. Useful
                for SDK-driven setup scripts that want to configure
                brokering at pair time without a Helm rebuild.
            local_kc_jwks_url: Companion to ``local_kc_issuer_url`` — the
                JWKS endpoint URL.
            local_broker_client_id: Keycloak client ID used for
                token-exchange brokering. The 4 brokering fields must
                be supplied together; partial sets are refused by the
                server with a 422 naming the missing field(s).
            local_broker_client_secret: Keycloak client secret (or
                DataHub secret URN) paired with ``local_broker_client_id``.

        Returns:
            Federation record reflecting the post-/pair state.
        """
        if preshared_key is None:
            preshared_key = str(uuid.uuid4())

        if remote_ips is None and remote_url is not None:
            parsed = urlparse(remote_url)
            host = parsed.hostname
            if host:
                remote_ips = [{"ip": host, "primary": True}]

        create_body: dict[str, Any] = {
            "remote_cluster_name": name,
            "role": role,
            "preshared_key": preshared_key,
        }
        if remote_ips is not None:
            create_body["remote_ips"] = remote_ips
        if callback_hostname is not None:
            create_body["callback_hostname"] = callback_hostname
        if remote_admin_token is not None:
            create_body["remote_admin_token"] = remote_admin_token
        # ENG-5822 — per-pair brokering inputs. Server-side atomic
        # validator refuses partial sets, so include only when all 4
        # are supplied (we let the server emit the validation error
        # so callers get one canonical source-of-truth for the contract).
        if local_kc_issuer_url is not None:
            create_body["local_kc_issuer_url"] = local_kc_issuer_url
        if local_kc_jwks_url is not None:
            create_body["local_kc_jwks_url"] = local_kc_jwks_url
        if local_broker_client_id is not None:
            create_body["local_broker_client_id"] = local_broker_client_id
        if local_broker_client_secret is not None:
            create_body["local_broker_client_secret"] = local_broker_client_secret

        created = self.client._request(
            "POST",
            "/cluster/federations",
            json=create_body,
        )
        if not isinstance(created, dict) or "id" not in created:
            raise TypeError(
                f"Expected POST /cluster/federations to return a dict with 'id', "
                f"got: {type(created).__name__}"
            )

        # Receivers wait for the initiator's /pair handshake — they don't
        # call /pair themselves. The bash recipe (00_pair_federation.sh)
        # creates a receiver-role record on the central cluster and stops
        # there; only the initiator drives the handshake. Mirror that here.
        if role == "receiver":
            return Federation.model_validate(created)

        federation_id = created["id"]
        paired = self.client._request(
            "POST",
            f"/cluster/federations/{federation_id}/pair",
        )
        return Federation.model_validate(paired)

    def __getitem__(self, name: str) -> "FederationProxy":
        """``client.federations["ORION"]`` — proxy for sub-resource access."""
        return FederationProxy(client=self.client, federations_api=self, name=name)

    def _resolve_id(self, name: str) -> str:
        """Resolve a federation by name to its UUID id.

        Walks the federation list once; result is cached on the
        ``FederationProxy`` after first resolution so ``users.add`` /
        ``users.revoke`` don't refetch.
        """
        body = self.client._request("GET", "/cluster/federations")
        items: List[Any] = []
        if isinstance(body, dict):
            raw = body.get("items")
            if isinstance(raw, list):
                items = raw
        elif isinstance(body, list):
            items = body
        for item in items:
            if isinstance(item, dict) and item.get("remote_cluster_name") == name:
                return str(item["id"])

        from ..exceptions import KamiwazaError

        raise KamiwazaError(
            f"No federation named {name!r} on this cluster. "
            "List federations with client.federations.list() (T5.x in WS-M2)."
        )


class FederationProxy:
    """Sub-resource accessor for a single named federation.

    Lazily resolves the federation's id on first sub-resource use so that
    indexed access (``client.federations["ORION"]``) doesn't cost a
    round-trip by itself. The id is cached on the proxy after first
    resolution.
    """

    def __init__(
        self,
        *,
        client: Any,
        federations_api: FederationsAPI,
        name: str,
    ) -> None:
        self._client = client
        self._federations_api = federations_api
        self.name = name
        self._cached_id: Optional[str] = None

    @property
    def users(self) -> "FederationUsersAPI":
        return FederationUsersAPI(proxy=self)

    def probe(self) -> ClusterCapabilities:
        """Probe this federation peer's capabilities via the mesh (T5.21).

        Routes ``GET /api/cluster/cluster_capabilities`` through the local
        mesh proxy at ``/api/mesh/{name}/...``. The mesh proxy resolves
        ``name`` to the federation, applies the federation:operator ReBAC
        guard, signs the request with the local cluster's HMAC, and
        forwards to the remote cluster.

        The federation selector is the cluster name itself — no separate
        federation-id resolution round-trip required.
        """
        body = self._client._request(
            "GET",
            f"/mesh/{self.name}/api/cluster/cluster_capabilities",
        )
        return ClusterCapabilities.model_validate(body)

    def _id(self) -> str:
        cached = self._cached_id
        if cached is None:
            cached = self._federations_api._resolve_id(self.name)
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
        request, the receiver's ``BrokeringService`` auto-provisions the
        local KC user record and seeds the supplied ``initial_tuples`` as
        ReBAC grants.

        Args:
            external_id: ``"<username>@<peer-cluster-uuid>"`` format.
            initial_tuples: ReBAC tuples to seed at provisioning. Each
                tuple is a dict with ``subject`` / ``relation`` /
                ``object`` keys.

        Returns:
            Newly-created BrokeredUser record (``auto_provisioned=False``
            until the user makes their first mesh request).
        """
        body: dict[str, Any] = {"external_id": external_id}
        if initial_tuples is not None:
            body["initial_tuples"] = initial_tuples

        result = self._client._request(
            "POST",
            f"/cluster/federations/{self._proxy._id()}/users",
            json=body,
        )
        return BrokeredUser.model_validate(result)
