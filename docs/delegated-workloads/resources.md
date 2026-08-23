# Protected resource integration

A protected resource keeps its existing service, data, and trust boundaries.
Integration adds an immutable platform descriptor, bounded resource-specific
adapters, and a guard before protected application code. It does not move the
resource into Core or require a new capability type, grant entity, run model,
or consumer branch.

The runnable reference is
[`examples/delegated_resource_server`](../../examples/delegated_resource_server/README.md).
It is a separately deployable ASGI document service with no extension, Tomo,
or operator dependency at runtime.

## Author the descriptor first

Define the authority contract before writing the handler integration. A v1
descriptor declares:

- one stable resource type and immutable descriptor version;
- one or more credential-free HTTPS audience origins;
- a bounded canonical resource-ID schema;
- closed action names and allowed HTTP methods;
- effect and approval classification owned by the descriptor;
- quota dimensions and versioned policy/quota adapter IDs;
- optional broker operation types and result normalization;
- the supported guard contract and compatibility versions; and
- staged lifecycle state for registrar review.

Use only the two supported classifications:

| Effect | Approval |
| --- | --- |
| `read` | `none` |
| `mutation` | `exact_it_approval` |

A caller or resource request cannot override this classification. Changing a
mutation to a read, removing approval, widening a method, or substituting an
adapter creates a new revision and requires platform authorization. Authority
growth also requires fresh member consent.

## Implement bounded adapters

Resource-specific behavior belongs behind the stable SDK ports in
`kamiwaza_sdk.delegated_workloads.adapters`:

- `ResourceCanonicalizer` validates one external identifier and returns its
  stable canonical form. Its request digest covers the exact semantic payload
  without credentials or caller-selected classification.
- `ResourceEntitlementAdapter` evaluates current resource-specific permission
  for the already composed member subject and workload actor.
- `QuotaAdapter` derives bounded resource units from trusted request context.
  It never accepts caller-authored limits, ledger subjects, or balances.
- `BrokerOperationAdapter` executes only a closed registered operation inside
  the trusted broker boundary.
- `SafeResultNormalizer` returns a bounded credential-free result and rejects
  unknown shapes.

Every adapter exposes a stable `adapter_id` ending in an explicit version. An
ID change is a descriptor revision, not an in-place implementation swap.

```python
import hashlib
import json
from collections.abc import Mapping


class DocumentCanonicalizer:
    @property
    def adapter_id(self) -> str:
        return "document-canonicalizer:v1"

    def canonicalize(self, resource_id: object) -> str:
        if not isinstance(resource_id, str) or not resource_id.startswith("doc-"):
            raise ValueError("document identifier is invalid")
        return "document:" + resource_id

    def request_digest(self, request: Mapping[str, object]) -> str:
        encoded = json.dumps(
            request,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()
```

Reject non-canonical IDs instead of guessing. Keep functions deterministic,
bounded, side-effect-free where the port requires it, and safe to invoke more
than once during planning and recheck.

## Install the resource guard

Build `ProtectedResourceGuard` with two dependencies:

1. a bounded provider of current Core public JWKS keys; and
2. `CoreResourceGuardHTTPClient`, which forwards the locally verified raw
   authority to Core's current decision and one-use consumption operations.

For ASGI, wrap each action with `DelegatedResourceASGI` and its exact
`ResourceGuardRegistration`:

```python
from kamiwaza_sdk.delegated_workloads import (
    CoreResourceGuardHTTPClient,
    ProtectedResourceGuard,
    ResourceGuardRegistration,
)
from kamiwaza_sdk.delegated_workloads.integrations.asgi import DelegatedResourceASGI

registration = ResourceGuardRegistration(
    resource_type="example.document",
    descriptor_version="v1",
    revision_id=active_revision_id,
    audience="https://documents.example",
    action="read",
    guard_contract_version="guard:v1",
)
decisions = CoreResourceGuardHTTPClient(core_protocol_url, session)
guard = ProtectedResourceGuard(jwks_provider, decisions)
application = DelegatedResourceASGI(handler, guard, registration)
```

Create a distinct wrapper for every registered action. Do not choose an action
from a request field. Keep health endpoints outside the protected router only
when they expose no resource data or authority behavior.

## What the guard enforces

Before the handler runs, the guard:

1. accepts only a dedicated `kz-effect-cap+jwt` capability;
2. verifies the trusted ES256 key, issuer, audience, lifetime, deadline,
   subject, actor, revision, role, instance, grant, run, claim, effect, fence,
   canonical resource, and action;
3. verifies the exact method, target URI, capability hash, body digest,
   freshness, replay ID, and proof-key thumbprint in the DPoP proof;
4. rejects every inbound `X-Kamiwaza-Delegated-*` header;
5. requests a current Core decision with the original capability, proof, and
   workload assertion;
6. compares the returned context with the locally verified authority;
7. consumes the one-use token and requires `status=executing` with the same
   context; and
8. installs an unconstructible `SealedDelegatedContext` before dispatch.

Any exception, timeout, unknown response, mismatch, replay, or dependency
failure becomes the same safe resource denial. There is no bearer-only,
member-only, stale-cache, or local-policy fallback.

## Handler contract

Read only identity and authority from the sealed context. The member subject
is `context.subject_id`; the distinct registered workload actor is
`context.actor_id`. Do not read either from query parameters, bodies, arbitrary
headers, or application sessions.

Recheck that route-derived identity equals the sealed canonical resource ID.
Validate the application payload independently, execute only the guarded
action, and normalize the response through the registered safe-result adapter.
Never echo the capability, proof, assertion, consumption token, broker handle,
credential, or raw Core response.

After external work, report the exact terminal outcome. A lost mutation
response or uncertain provider state is `ambiguous`; never replay it under a
new effect key. Reconciliation may inspect trusted provider evidence but may
not repeat the operation.

## Public keys and deployment

Cache only public JWKS material for a short bounded interval. Reject private
`d` coordinates, duplicate key IDs, unsupported algorithms, expired
verification windows, malformed documents, and refresh failure. Key rotation
must preserve a bounded verification overlap for already issued capabilities.

Run the service with its normal least-privilege identity. It needs network
access to the delegated Core protocol and its own dependencies, not a member
token, registrar credential, Kubernetes API permission, or provider secret.
Inject only the Core protocol URL, active resource revision ID, and exact
audience as non-secret runtime configuration.

See the [HTTP protocol](protocol.md) for non-Python implementations and the
[registrar guide](registrars.md) for descriptor activation.
