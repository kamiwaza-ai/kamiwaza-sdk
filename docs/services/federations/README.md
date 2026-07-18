# Federations Service

## Overview

The Federations service (`FederationsAPI`, `kamiwaza_sdk/services/federations.py`)
manages cluster-to-cluster federation pairings and cross-cluster access from the
SDK. Access it as `client.federations`. It covers the federation lifecycle
(pair, list, get, probe, disconnect), a per-federation proxy for sub-resources,
and brokered-user allowlisting. Cross-cluster **identity trust** is governed by
each federation's identity mode (`peer_kc`, `shared_idp`, or `receiver_realm`);
see the platform
[Identity Trust Modes](https://docs.kamiwaza.ai/federation/identity-trust-modes)
guide for the trust model. In `receiver_realm` mode the receiver provisions a
dedicated realm and issues guest credentials (see `guests` below).

## Methods

### `pair(name, role, *, remote_ips, preshared_key=None, shared_issuer_url=None, ...) -> Federation`

Create a federation pairing. `role` is the side being set up (`initiator` or
`receiver`). A pre-shared key is auto-generated (UUID4) when `preshared_key` is
`None`; supply your own to share a key out-of-band.

**Selecting the identity mode:**

- supplying `shared_issuer_url` (with `shared_jwks_url` / `shared_ca_pem`) creates
  a **receiver-controlled `shared_idp`** federation;
- supplying `realm_scope` (e.g. `"per_federation"`) creates a
  **receiver-owned `receiver_realm`** federation — the receiver provisions a
  dedicated `fed-<id>` Keycloak realm at pairing and issues its own guest
  credentials (design §15). Mutually exclusive with the `shared_*` inputs;
- omitting both creates a legacy source-trusted **`peer_kc`** federation
  (subject to the cluster's `ALLOW_UNTRUSTED_FEDERATION` policy).

```python
# shared_idp (receiver-controlled)
fed = client.federations.pair(
    name="ORION",
    role="receiver",
    remote_ips=[{"ip": "10.0.0.5", "primary": True}],
    preshared_key=shared_psk,
    shared_issuer_url="https://idp.example/realms/federated",
    shared_jwks_url="https://idp.example/realms/federated/protocol/openid-connect/certs",
)
```

### `list() -> List[Federation]`

List all federations on this cluster (`GET /cluster/federations`). Handles both a
bare list and an `{"items": [...]}` response shape.

### `get(federation_id) -> Federation`

Fetch a single federation by id (`GET /cluster/federations/{id}`).

### `client.federations[name] -> FederationProxy`

Index by name to get a proxy for one federation's sub-resources. The proxy
resolves the federation id on first use and caches it.

```python
orion = client.federations["ORION"]
caps  = orion.probe()               # peer capabilities over the mesh
orion.users.add(external_id="alice@peer-uuid")
orion.disconnect()
```

### `FederationProxy.probe() -> ClusterCapabilities`

Probe the peer's capabilities through the local mesh proxy. Mesh egress is
authenticated-only.

### `FederationProxy.disconnect(*, force=False) -> Any`

Disconnect (unpair) the federation (`POST /cluster/federations/{id}/disconnect`).
`force=True` tears down without waiting for the peer.

### `FederationProxy.users.add(external_id, *, initial_tuples=None) -> BrokeredUser`

Allowlist a brokered remote user on this (receiver) cluster
(`POST /cluster/federations/{id}/users`). `external_id` identifies the remote
subject; `initial_tuples` seeds the ReBAC grants the user should have on this
cluster.

### `FederationProxy.guests.enroll(external_id, *, initial_tuples=None) -> FederationGuest`

**`receiver_realm` only** (ENG-8213 Alt D). Enroll a source user as a guest in
the receiver's dedicated `fed-<id>` realm and mint a durable offline credential
(`POST /cluster/federations/{id}/guests`). The returned `FederationGuest` carries
`offline_token` — **returned once**; it is never re-fetchable, so persist it and
deliver it to the source cluster out-of-band. `initial_tuples` seeds the guest's
ReBAC grants at enrollment.

```python
orion = client.federations["ORION"]                # a receiver_realm federation
guest = orion.guests.enroll("carol@src-uuid")
print(guest.realm, guest.offline_token)            # fed-<id>, <credential — save now>
orion.guests.revoke("carol@src-uuid")              # disable the guest (FR-79)
```

### `FederationProxy.guests.revoke(external_id) -> Any`

Revoke an enrolled guest by disabling its allowlist row
(`POST /cluster/federations/{id}/guests/{external_id}/revoke`). Subsequent mesh
calls presenting that guest's credential are refused at the receiver's ingress.

## The `kamiwaza-fed` CLI

The SDK ships an operator/test utility, `kamiwaza-fed`
(`kamiwaza_sdk.seeding.federation`), that scripts shared_idp stand-up and ReBAC
access seeding. Groups: `access` (ReBAC grants), `fed` (pair / status /
allow-user / unpair), `dataset` / `gate` / `attr` (gated-retrieval setup), and
`idp`.

> **The `idp` group is DEV/TEST-only.** Its `bootstrap` / `persona` subcommands
> drive the Keycloak **admin** API, which the platform ingress does not expose —
> they need direct Keycloak access (a port-forward). In production, provision the
> shared realm through the auth chart's install-time Keycloak init-Job pipeline,
> not this command.

Secrets are always read from environment variables, never passed on the command
line.
