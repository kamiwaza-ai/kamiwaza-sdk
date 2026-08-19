# Federations Service

## Overview

The Federations service (`FederationsAPI`, `kamiwaza_sdk/services/federations.py`)
manages cluster-to-cluster federation pairings and cross-cluster access from the
SDK. Access it as `client.federations`. It covers the federation lifecycle
(pair, list, get, probe, disconnect), a per-federation proxy for sub-resources,
and brokered-user allowlisting. Cross-cluster **identity trust** is governed by
each federation's identity mode (`peer_kc` or `shared_idp` in Kamiwaza 1.2.0);
see the platform
[Identity Trust Modes](https://docs.kamiwaza.ai/federation/identity-trust-modes)
guide for the trust model. The `receiver_realm` value and the SDK's guest helper
symbols are reserved for a future receiver-owned identity workflow. Kamiwaza
1.2.0 rejects that mode with `identity_mode_unsupported`.

## Methods

### `pair(name, role, *, remote_ips, preshared_key=None, shared_issuer_url=None, ...) -> Federation`

Create a federation pairing. `role` is the side being set up (`initiator` or
`receiver`). A pre-shared key is auto-generated (UUID4) when `preshared_key` is
`None`; supply your own to share a key out-of-band.

**Selecting the identity mode:**

- supplying `shared_issuer_url` (with `shared_jwks_url` / `shared_ca_pem`) creates
  a **receiver-controlled `shared_idp`** federation;
- do not supply `realm_scope` on Kamiwaza 1.2.0. It selects the reserved
  `receiver_realm` mode, which the server rejects as unsupported. It is mutually
  exclusive with the `shared_*` inputs;
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
federation read or list lookup. Use this for receiver-side user and disconnect
operations when pairing has replaced an operator-entered label with
the peer's advertised cluster name. IDs may be UUID strings or `uuid.UUID`
objects; invalid IDs raise `ValueError` before any request is made.

```python
receiver = client.federations.by_id(receiver_federation_id)
receiver.users.add(external_id="carol@src-uuid")
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

### Reserved guest helpers

`FederationProxy.guests.enroll(...)` and `.revoke(...)` are forward-looking SDK
symbols for the unavailable `receiver_realm` workflow. They are not part of the
supported Kamiwaza 1.2.0 server contract and must not be used against a 1.2.0
cluster.

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

Undo a disconnect this cluster performed, re-admitting the peer's brokered users
(`POST /cluster/federations/{id}/reconnect`). Accepts a `DISCONNECTED`
federation and nothing else (409 `federation_not_disconnected`) — it reverses a
local disconnect, where the realm, key and truststore entry were all preserved;
re-pairing is the general flow. Returns the count of users `restored` plus the
best-effort Keycloak outcomes.

### Reserved source-side credential resolution

The SDK retains forward-looking configuration symbols for a future
`receiver_realm` credential. Kamiwaza 1.2.0 does not issue that credential or
accept `receiver_realm` federations, so do not configure these symbols for a
1.2.0 deployment. `peer_kc` and `shared_idp` calls continue to use their
documented identity paths without this header.

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
