# Delegated credential broker

The delegated credential client performs one registered provider operation for
a member-owned binding without exposing the provider credential to workload
code. Core owns the binding, exact-use authority, provider adapter, destination
policy, durable lease, revocation, and audit record. The SDK owns only typed
discovery and dispatch.

> This is an implementation checkpoint, not a production-readiness claim.
> Keep consuming applications disabled until complete delegated-workload
> discovery and deployed conformance report the credential family ready.

> **Important:** Core deployments must compose effect-issued broker handles
> with their closed trusted-adapter registry. The client types do not make that
> deployment seam ready or provide a fallback when it is absent.

## Discover safe bindings in the member session

Binding discovery is a member operation. Give `CredentialBroker` the same
workload transport used by the executor and, separately, an authenticated
member session for discovery:

```python
from uuid import UUID

from kamiwaza_sdk.delegated_workloads import CredentialBroker

broker = CredentialBroker(
    "https://kamiwaza.example/api/v1/delegated-workloads",
    transport,
    member_session=member_session,
)
bindings = broker.list_bindings(
    UUID("11111111-1111-4111-8111-111111111111"),
    operation="documents.append",
)
```

The member session is used only by `list_bindings`; it is never copied into a
workload request. Discovery returns safe summaries: binding ID, provider and
display names, allowed operation IDs, mode, status, revocation support, and an
optional maximum ephemeral TTL. It does not return provider account IDs,
connection IDs, scopes, access tokens, refresh material, or revocation handles.

Choose only an active binding that explicitly lists the intended operation.
Core rechecks the current member, client, grant, binding, operation, and expiry
when the effect is reserved and used. Do not cache a discovery response as an
authorization decision or substitute a different binding after a denial.

## Reserve and execute one exact use

Credential use is an exact protected effect. Include the selected binding and
registered HTTPS destination in `EffectReservationRequest`. Its
`effect_digest` must be the canonical digest produced for the registered
resource and request contract; do not invent a local digest recipe.

After Core returns an allowed, reserved effect with both an effect capability
and broker handle, construct the closed operation request and process-local
lease:

```python
from kamiwaza_sdk.delegated_workloads import (
    CredentialOperationParameters,
    CredentialUseRequest,
    TrustedAdapterLease,
)

use = CredentialUseRequest(
    credential_binding_id=binding.id,
    operation_id="documents.append",
    request_digest=canonical_request_digest,
    parameters=CredentialOperationParameters(
        resource_id="doc-7",
        body={"text": "approved content"},
    ),
)
lease = TrustedAdapterLease.from_effect(effect, use)
receipt = broker.execute(lease)
```

`TrustedAdapterLease.from_effect` rejects denied, pending, or incomplete effect
reservations locally. Its handle and capability are intentionally private,
redacted, and non-pickleable. Do not reach into private fields, put the lease
on a queue, log it, or persist it. Create and use it inside the attested
executor process that owns the current claim and fence.

Parameters are closed to `params`, `body`, and `resource_id`. Nested content is
bounded, must use finite JSON-compatible scalar values, and rejects keys that
look like credentials. The operation ID selects a trusted registered adapter;
there is no SDK input for an HTTP method, provider base URL, arbitrary header,
credential, or generic proxy target.

`CredentialBroker.execute` deliberately marks the provider operation as
non-retryable. The transport does not replay it for a DPoP nonce challenge,
temporary provider response, timeout, or lost response. A caller must not
construct another lease or effect merely because no successful response was
received.

## Interpret the terminal receipt

A receipt contains a lease ID, correlation ID, bounded result, and exactly one
terminal status:

- `succeeded` means Core obtained and normalized a known successful result;
- `failed` means Core obtained a known terminal failure; and
- `ambiguous` means the provider may have performed the operation but the
  outcome cannot be proved.

Treat `ambiguous` as terminal for automatic execution. Preserve the correlation
and lease IDs for authorized reconciliation. Do not retry under a new effect
key, replacement run, binding, or local provider client. Expiry and cancellation
also do not prove that an external operation failed to happen.

Safe failures use typed delegated exceptions and content-minimized messages.
`CredentialBindingUnavailable`, `CurrentAuthorityDenied`,
`ReplayRejected`, and `ProviderTransientFailure` are terminal for the current
use. `AmbiguousEffectOutcome` is never retryable. A provider-transient
classification does not authorize SDK transport retry of the protected
operation.

## Brokered and ephemeral modes

`brokered` is the preferred mode. Core resolves and uses the member's provider
credential inside its trusted boundary and returns only a bounded result.

`ephemeral_token` is a Core-owned provider-adapter fallback, not a token API.
It is allowed only for an exact provider, operation, audience, destination,
scope subset, effect, and run, with programmatic provider revocation and a
maximum lifetime of 900 seconds. The SDK receives neither the token nor its
revocation handle. Grant or binding revocation, cancellation, and every
terminal run state request immediate provider revocation; the remaining
provider expiry window stays explicit in consent and audit.

There is no raw-secret mode, public provider-token method, compatibility flag,
or generic OAuth fallback in this client. An unsupported mode fails before a
provider resolver or transport is invoked.

## Secret absence and process cleanup

Provider credentials must not enter automation definitions, queues, prompts,
model requests, application databases, durable environments, logs, traces,
audits, exports, errors, or reusable configuration. The SDK validates and
redacts its authority-bearing types to prevent common accidental exposure, but
application code must still avoid introspection and private-field access.

Python cannot guarantee deterministic zeroization of every prior immutable
heap value. The enforceable contract is narrower: the delegated SDK never
receives provider material through its public API and does not serialize, log,
reflect, persist, or return broker authority. Close the shared workload
transport when the revision stops so its assertion and proof-key lifecycle can
retire; closure is not a substitute for provider revocation.

See [Delegated workload client](client.md) for run, effect, and ambiguity
lifecycle. Core's operator and security guidance owns adapter registration,
destination enforcement, audit, and reconciliation.
