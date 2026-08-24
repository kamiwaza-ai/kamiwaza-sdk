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

from ..schemas.federation import (
    BrokeredUser,
    ClusterCapabilities,
    Federation,
    FederationGuest,
)
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
        shared_issuer_url: Optional[str] = None,
        shared_jwks_url: Optional[str] = None,
        shared_ca_pem: Optional[str] = None,
        realm_scope: Optional[str] = None,
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
            local_broker_client_secret: DataHub secret URN
                (``urn:li:dataHubSecret:...``) for the Keycloak broker
                client secret. The server resolves this URN via
                CatalogService at pair time and ships the raw secret
                to the peer. **URN-only — raw secrets are refused**
                with a 422 because they would land in API logs,
                network traces, and DB rows in plaintext. Operators
                store the raw secret in DataHub first (via the
                secrets API) and supply the URN here. Mirrors the
                PSK secret-handling path.
            shared_issuer_url: ENG-8213 shared_idp (Alt C) — supplying it
                creates the federation in the receiver-controlled shared_idp
                mode (both clusters trust this shared realm). ``shared_jwks_url``
                is derived server-side when omitted; ``shared_ca_pem`` pins the
                JWKS-fetch trust root for a self-signed realm.
            realm_scope: ENG-8213 receiver_realm (Alt D) — supplying it (e.g.
                ``"per_federation"``) creates the federation in the
                receiver-owned-realm mode: the receiver provisions a dedicated
                ``federation-<id>`` Keycloak realm at pairing and mints its own guest
                credentials via ``kz.federations[name].guests.enroll(...)``.
                Mutually exclusive with the shared_idp inputs.

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
        # ENG-8213 — shared_idp (Alt C). Supplying ``shared_issuer_url`` creates
        # the federation in the receiver-controlled shared_idp mode (both
        # clusters trust this shared realm) instead of the legacy source-trusted
        # peer_kc mode, and it is NOT gated by ALLOW_UNTRUSTED_FEDERATION.
        # ``shared_jwks_url`` is derived from the issuer server-side when omitted;
        # ``shared_ca_pem`` pins the JWKS-fetch trust root for a self-signed realm.
        if shared_issuer_url is not None:
            create_body["shared_issuer_url"] = shared_issuer_url
        if shared_jwks_url is not None:
            create_body["shared_jwks_url"] = shared_jwks_url
        if shared_ca_pem is not None:
            create_body["shared_ca_pem"] = shared_ca_pem
        # ENG-8213 — receiver_realm (Alt D). Supplying ``realm_scope`` creates the
        # federation in the receiver-owned-realm mode: the receiver provisions a
        # dedicated ``federation-<id>`` Keycloak realm at pairing and mints its own guest
        # credentials (design section 15). Distinct from shared_idp — no shared
        # realm is trusted; identity is minted by the receiver.
        if realm_scope is not None:
            create_body["realm_scope"] = realm_scope

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

    def _list_raw(self) -> List[Any]:
        """GET the federation list and return the raw item dicts.

        The server may return a bare list or ``{"items": [...]}``; both are
        normalized to a list here. Shared by :meth:`list` (which validates each
        item into a ``Federation``) and :meth:`_resolve_id` (which only needs
        the ``id`` / ``remote_cluster_name`` identity fields and must not depend
        on the full ``Federation`` schema being satisfied).
        """
        body = self.client._request("GET", "/cluster/federations")
        if isinstance(body, dict):
            raw = body.get("items")
            return raw if isinstance(raw, list) else []
        if isinstance(body, list):
            return body
        return []

    def list(self) -> List[Federation]:
        """List all federations on this cluster.

        ``GET /cluster/federations`` — the widened any-authenticated posture
        surface (mode, issuer, PAIRED state, brokering config). The server may
        return a bare list or ``{"items": [...]}``; both are handled.
        """
        return [Federation.model_validate(item) for item in self._list_raw()]

    def get(self, federation_id: Any) -> Federation:
        """Fetch a single federation by id (``GET /cluster/federations/{id}``)."""
        body = self.client._request("GET", f"/cluster/federations/{federation_id}")
        return Federation.model_validate(body)

    def by_id(
        self,
        federation_id: Any,
        *,
        remote_name: Optional[str] = None,
    ) -> "FederationProxy":
        """Return a proxy bound to one authoritative federation id.

        No federation read is performed. Invalid UUIDs raise ``ValueError``.
        ``remote_name`` is optional and only selects the mesh route and
        credential key used by :meth:`probe`; ID-addressed control-plane
        operations always use ``federation_id``.
        """
        try:
            bound_id = str(uuid.UUID(str(federation_id).strip()))
        except (AttributeError, TypeError, ValueError):
            raise ValueError("federation_id must be a valid UUID") from None
        return FederationProxy(
            client=self.client,
            federations_api=self,
            name=remote_name or bound_id,
            federation_id=bound_id,
        )

    def __getitem__(self, name: str) -> "FederationProxy":
        """``client.federations["ORION"]`` — proxy for sub-resource access."""
        return FederationProxy(client=self.client, federations_api=self, name=name)

    def _resolve_id(self, name: str) -> str:
        """Resolve a federation by name to its UUID id.

        Walks the raw federation list once and matches on
        ``remote_cluster_name``; result is cached on the ``FederationProxy``
        after first resolution so ``users.add`` / ``users.revoke`` don't
        refetch. Deliberately reads the raw list dicts rather than constructing
        full ``Federation`` models: id-resolution only needs the ``id`` +
        ``remote_cluster_name`` identity fields and must not fail if the list
        endpoint omits an unrelated ``Federation`` field (e.g. ``status``).
        """
        for item in self._list_raw():
            if isinstance(item, dict) and item.get("remote_cluster_name") == name:
                fed_id = item.get("id")
                if fed_id:
                    return str(fed_id)

        from ..exceptions import KamiwazaError

        raise KamiwazaError(
            f"No federation named {name!r} on this cluster. "
            "List federations with client.federations.list()."
        )


class FederationProxy:
    """Sub-resource accessor for a single federation.

    Name-indexed access lazily resolves the federation's id on first
    sub-resource use. ``FederationsAPI.by_id`` seeds the authoritative id
    directly, without a federation read. The id is cached on the proxy after
    first resolution.
    """

    def __init__(
        self,
        *,
        client: Any,
        federations_api: FederationsAPI,
        name: str,
        federation_id: Optional[str] = None,
    ) -> None:
        self._client = client
        self._federations_api = federations_api
        self.name = name
        self._cached_id = federation_id

    @property
    def users(self) -> "FederationUsersAPI":
        return FederationUsersAPI(proxy=self)

    @property
    def guests(self) -> "FederationGuestsAPI":
        """Receiver_realm guest management (ENG-8213 Alt D). Only meaningful for
        federations created with ``realm_scope`` — the receiver mints guest
        credentials in its dedicated ``federation-<id>`` realm."""
        return FederationGuestsAPI(proxy=self)

    def _mesh_headers(self) -> dict[str, str]:
        """S8 (design §7.5) — attach the per-target receiver-issued federation
        credential (``X-KZ-Federation-Credential``) when one is configured for
        this federation, so a mesh call to a ``receiver_realm`` target is
        validated against the receiver's own ``federation-<id>`` realm. Empty for
        peer_kc / shared_idp targets (unchanged local-identity mesh call)."""
        from .federation_credentials import federation_credential_headers

        return federation_credential_headers(self.name)

    def probe(self) -> ClusterCapabilities:
        """Probe this federation peer's capabilities via the mesh (T5.21).

        Routes ``GET /api/cluster/cluster_capabilities`` through the local
        mesh proxy at ``/api/mesh/{name}/...``. The mesh proxy resolves
        ``name`` to the federation, signs the request with the local
        cluster's HMAC, and forwards to the remote cluster. (Mesh egress is
        authenticated-only; cross-cluster authorization is receiver-controlled
        per F10 — the initiator ``federation:operator`` gate was dropped in
        ENG-8571.)

        For a ``receiver_realm`` target the per-target federation credential is
        attached (§7.5); it is a no-op for other modes.

        The federation selector is the proxy's configured name. An ID-bound
        proxy without ``remote_name`` uses the federation UUID instead; core
        accepts either selector and no separate resolution round trip is
        required.
        """
        headers = self._mesh_headers()
        body = self._client._request(
            "GET",
            f"/mesh/{self.name}/api/cluster/cluster_capabilities",
            **({"headers": headers} if headers else {}),
        )
        return ClusterCapabilities.model_validate(body)

    def disconnect(self, *, force: bool = False) -> Any:
        """Disconnect (unpair) this federation.

        ``POST /cluster/federations/{id}/disconnect``. A name-bound proxy
        resolves the federation id on first use; an ID-bound proxy uses its
        supplied id directly. ``force=True`` tears down without waiting for the
        peer's acknowledgement (use when the peer is already gone). Returns the
        server's confirmation payload.
        """
        params = {"force": "true"} if force else None
        return self._client._request(
            "POST",
            f"/cluster/federations/{self._id()}/disconnect",
            params=params,
        )

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


class FederationGuestsAPI:
    """Receiver_realm guest management on a single federation (ENG-8213 Alt D).

    Unlike :class:`FederationUsersAPI` (the mode-agnostic allowlist), a
    receiver_realm receiver *issues* the credential: enrolling a guest
    provisions it in the receiver-owned ``federation-<id>`` realm and mints a durable
    offline token returned once (design section 15.2). Only meaningful for
    federations paired with ``realm_scope``.
    """

    def __init__(self, *, proxy: FederationProxy) -> None:
        self._proxy = proxy
        self._client = proxy._client

    def enroll(
        self,
        external_id: str,
        *,
        initial_tuples: Optional[List[Any]] = None,
        identity_proof: Optional[dict[str, Any]] = None,
    ) -> FederationGuest:
        """Enroll a source user as a guest and mint its offline credential.

        Args:
            external_id: The source user's identifier (``"<username>@<peer-
                cluster-uuid>"`` format), enrolled into the receiver's
                ``federation-<id>`` realm.
            initial_tuples: ReBAC tuples to seed for the guest at enrollment.
                Each tuple is a dict with ``subject`` / ``relation`` /
                ``object`` keys. Forwarded only when supplied.
            identity_proof: Out-of-band identity proof validated by the
                federation's ``verification`` seam (design §7.4) BEFORE the
                credential is minted. Shape depends on the mode: ``manual``
                accepts an optional ``{"attestation": "..."}``; ``mtls`` requires
                ``{"client_cert_pem": "..."}`` chaining to the receiver's trust
                CA. Forwarded only when supplied (the ``manual`` default needs
                nothing); a proof the receiver rejects fails the call with 400.

        Returns:
            :class:`FederationGuest` carrying the ``offline_token`` — **returned
            once**; persist it out-of-band, it cannot be re-fetched.
        """
        body: dict[str, Any] = {"external_id": external_id}
        if initial_tuples is not None:
            body["initial_tuples"] = initial_tuples
        if identity_proof is not None:
            body["identity_proof"] = identity_proof

        result = self._client._request(
            "POST",
            f"/cluster/federations/{self._proxy._id()}/guests",
            json=body,
        )
        return FederationGuest.model_validate(result)

    def revoke(self, external_id: str) -> Any:
        """Revoke an enrolled guest by disabling its allowlist row (FR-79).

        Subsequent mesh calls presenting that guest's credential are refused at
        the receiver's ingress. Returns the server's confirmation payload.
        """
        return self._client._request(
            "POST",
            f"/cluster/federations/{self._proxy._id()}/guests/{external_id}/revoke",
        )
