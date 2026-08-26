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
guide for the trust model. Supplying `realm_scope="per_federation"` selects the
receiver-owned `receiver_realm` workflow; the receiver provisions a dedicated
realm and mints each guest's durable offline credential.

## Methods

### `pair(name, role, *, remote_ips, preshared_key=None, shared_issuer_url=None, ...) -> Federation`

Create a federation pairing. `role` is the side being set up (`initiator` or
`receiver`). A pre-shared key is auto-generated (UUID4) when `preshared_key` is
`None`; supply your own to share a key out-of-band.

**Selecting the identity mode:**

- supplying `shared_issuer_url` (with `shared_jwks_url` / `shared_ca_pem`) creates
  a **receiver-controlled `shared_idp`** federation;
- supplying `realm_scope="per_federation"` creates a **receiver-owned
  `receiver_realm`** federation. The receiver must be paired with the same
  `realm_scope` value on the initiator row;
- without `shared_issuer_url`, Core follows the legacy source-trusted
  **`peer_kc`** path. It creates the federation only when
  `ALLOW_UNTRUSTED_FEDERATION` permits that path; otherwise it fails with
  `untrusted_federation_disabled`.

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

### Guest helpers

`FederationProxy.guests.enroll(...)` mints a receiver-owned guest credential and
returns its `offline_token` exactly once. Persist that opaque value out of band;
the SDK and trace harness never decode or log it. Use
`FederationProxy.guests.revoke(external_id)` to disable the guest at the
receiver; subsequent mesh calls fail closed.

## Trust lifecycle (`client.cluster`)

Rotating the pre-shared key, replacing a rotated peer CA, and undoing a
disconnect are per-federation-id operations and hang off `client.cluster`
(`ClusterAPI`), not the name-keyed `FederationProxy`. These operations are
admin + native-realm. The peer-proven rotation protocol requires a Core build
containing ENG-10082; it is not part of the Core 1.2.0 contract described above.

### `client.cluster.rotate_preshared_key(federation_id) -> dict`

Stage K2 while K1 remains the active signer
(`POST /cluster/federations/{id}/rotate-preshared-key`). The response returns
the plaintext K2 exactly once, with its `fingerprint` and `generation`. Carry K2
and the fingerprint to the peer operator through an approved out-of-band secret
channel; never log either key. If that one-time response is lost, inspect status
and abort the identified STAGED generation before retrying.

### `client.cluster.get_key_rotation_status(federation_id) -> dict`

Read the current phase, generation, and active/alternate fingerprints without
returning key material
(`GET /cluster/federations/{id}/key-rotation-status`). This is the recovery
surface for lost operator responses.

### `client.cluster.adopt_preshared_key_rotation(federation_id, *, preshared_key, fingerprint) -> dict`

Stage the initiator's exact K2 on the peer without changing its active signer
(`POST /cluster/federations/{id}/adopt-preshared-key-rotation`). Repeating the
same adoption is idempotent; a different open generation is refused rather than
overwritten.

### `client.cluster.activate_key_rotation(federation_id, *, fingerprint) -> dict`

Ask Core to activate K2
(`POST /cluster/federations/{id}/activate-key-rotation`). Core first proves to
the peer, using K2, that both sides hold the same staged key; only then does it
switch the local signer. A caller acknowledgement is not accepted as evidence.

### `client.cluster.complete_key_rotation(federation_id, *, fingerprint) -> dict`

Retire K1
(`POST /cluster/federations/{id}/complete-key-rotation`). Core closes the peer's
K1 acceptance window first using a current-K2 proof, then closes the local
window. Repeating the same completion is idempotent.

### `client.cluster.abort_key_rotation(federation_id, *, fingerprint, generation) -> dict`

Discard exactly one STAGED K2 generation without changing K1
(`POST /cluster/federations/{id}/abort-key-rotation`). Abort is recovery for an
unactivated generation only; it is not a rollback after activation.

```python
initiator_id = initiator.federations.get(...).id
receiver_id = receiver.federations.get(...).id
staged = initiator.cluster.rotate_preshared_key(initiator_id)

# Transfer these two values through the approved out-of-band secret channel.
receiver.cluster.adopt_preshared_key_rotation(
    receiver_id,
    preshared_key=staged["preshared_key"],
    fingerprint=staged["fingerprint"],
)
initiator.cluster.activate_key_rotation(
    initiator_id, fingerprint=staged["fingerprint"]
)
initiator.cluster.complete_key_rotation(
    initiator_id, fingerprint=staged["fingerprint"]
)
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

### Receiver-realm credential resolution

The source side carries the receiver-issued offline credential through the
federation-credential header. It is intentionally opaque: do not decode it,
place it in logs, or include it in diagnostic trace files. `peer_kc` and
`shared_idp` calls continue to use their documented identity paths.

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
