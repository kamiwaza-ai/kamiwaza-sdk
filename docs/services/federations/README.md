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
  dedicated `federation-<id>` Keycloak realm at pairing and issues its own guest
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

### `by_id(federation_id, *, remote_name=None) -> FederationProxy`

Return a proxy bound directly to an authoritative federation id, without a
federation read or list lookup. Use this for receiver-side guest, user, and
disconnect operations when pairing has replaced an operator-entered label with
the peer's advertised cluster name. IDs may be UUID strings or `uuid.UUID`
objects; invalid IDs raise `ValueError` before any request is made.

```python
receiver = client.federations.by_id(receiver_federation_id)
guest = receiver.guests.enroll("carol@src-uuid")
receiver.guests.revoke(guest.external_id)
```

For a mesh probe, prefer the name-keyed proxy below. If the caller already has
both values, `by_id(id, remote_name="ORION")` uses the exact id for control-plane
operations and `ORION` as the mesh selector and name-keyed federation-credential
lookup. Without `remote_name`, probing uses the id as both the mesh selector and
credential key; core accepts a federation UUID selector, and the matching
environment-variable suffix is the upper-cased UUID with hyphens replaced by
underscores.

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
the receiver's dedicated `federation-<id>` realm and mint a durable offline credential
(`POST /cluster/federations/{id}/guests`). The returned `FederationGuest` carries
`offline_token` — **returned once**; it is never re-fetchable, so persist it and
deliver it to the source cluster out-of-band. `initial_tuples` seeds the guest's
ReBAC grants at enrollment.

```python
receiver = client.federations.by_id(receiver_federation_id)
guest = receiver.guests.enroll("carol@src-uuid")
print(guest.realm, guest.offline_token)            # federation-<id>, <credential — save now>
receiver.guests.revoke(guest.external_id)           # disable the guest (FR-79)
```

### `FederationProxy.guests.revoke(external_id) -> Any`

Revoke an enrolled guest by disabling its allowlist row
(`POST /cluster/federations/{id}/guests/{external_id}/revoke`). Subsequent mesh
calls presenting that guest's credential are refused at the receiver's ingress.

## Trust lifecycle (`client.cluster`)

Rotating the pre-shared key, replacing a rotated peer CA, and undoing a
disconnect are per-federation-id operations and hang off `client.cluster`
(`ClusterAPI`), not the name-keyed `FederationProxy`. All four are admin +
native-realm.

### `client.cluster.rotate_preshared_key(federation_id) -> dict`

Open a rotation window (`POST /cluster/federations/{id}/rotate-preshared-key`).
**Additive** — the outgoing key keeps verifying until the window is closed, so
this cannot sever the mesh and takes no acknowledgement. The plaintext key comes
back on `preshared_key` and is **not retrievable afterwards**; carry it to the
peer's operator out of band. Refuses `rotation_already_in_flight` (409) while a
window is open — opening twice would overwrite the outgoing key and strand a
peer still signing with the original.

### `client.cluster.complete_key_rotation(federation_id, *, acknowledged) -> dict`

Close the window, retiring the outgoing key
(`POST /cluster/federations/{id}/complete-key-rotation`). **Subtractive** — a
peer still signing with the old key stops working the moment this returns.
`acknowledged` must be `True` and is not a check: the cluster cannot observe
whether the peer adopted the new key, which is exactly why the operator has to
assert it. Refuses `rotation_acknowledgement_required` (400) and
`no_rotation_in_flight` (409).

```python
fed_id = client.federations.get(...).id
opened = client.cluster.rotate_preshared_key(fed_id)
print(opened["preshared_key"])                 # save now — shown once
# ... deliver it to the peer's operator, and only then:
client.cluster.complete_key_rotation(fed_id, acknowledged=True)
```

### `client.cluster.refresh_peer_ca(federation_id, *, ca_pem, acknowledged_fingerprint) -> dict`

Replace the stored peer CA after the peer rotated its own
(`POST /cluster/federations/{id}/refresh-peer-ca`). `acknowledged_fingerprint`
must match the SHA-256 of the whitespace-normalised `ca_pem`. That is not a
security check — you supply both halves — it forces the out-of-band comparison
with the peer's operator. Refuses `peer_ca_required` /
`fingerprint_acknowledgement_required` (400) and
`fingerprint_acknowledgement_mismatch` (409); the refusal carries the supplied
CA's real `fingerprint` so it can be verified out of band.

### `client.cluster.reconnect_federation(federation_id) -> dict`

Undo a disconnect this cluster performed, re-admitting the peer's guests
(`POST /cluster/federations/{id}/reconnect`). Accepts a `DISCONNECTED`
federation and nothing else (409 `federation_not_disconnected`) — it reverses a
local disconnect, where the realm, key and truststore entry were all preserved;
re-pairing is the general flow. Returns the count of guests `restored` plus the
best-effort Keycloak outcomes.

### Source-side credential resolution (`receiver_realm`)

A source user targeting a `receiver_realm` federation calls the receiver over the
mesh with the **receiver-issued** credential (obtained out of band from
`guests.enroll`), not their local login. Configure it per target and the SDK
attaches it automatically as `X-KZ-Federation-Credential` on mesh calls to that
federation:

- `KAMIWAZA_FEDERATION_CREDENTIAL_<NAME>` — env var, where `<NAME>` is the
  federation name upper-cased with non-alphanumerics mapped to `_`
  (`orion-prod` → `KAMIWAZA_FEDERATION_CREDENTIAL_ORION_PROD`).
- `KAMIWAZA_FEDERATION_CREDENTIAL_FILE` — path to a JSON file mapping federation
  name → credential (env var wins when both are set).

The local `KAMIWAZA_PAT` continues to serve local calls unchanged; targets in
other identity modes (`peer_kc` / `shared_idp`) are unaffected (no header added).

## The `kamiwaza-federation` CLI

The SDK ships an operator/test utility, `kamiwaza-federation`
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
