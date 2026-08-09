# Delegated workload client

The delegated-workload package exposes protocol clients for registered
workloads and protected resources. A workload acts on behalf of a member; it
does not become a member session. Core derives the member subject from durable
consent and keeps the registered workload or agent as a distinct actor.

> This surface is gated by delegated-workload discovery and the complete v1
> readiness contract. A successful internal read or run reservation does not
> by itself make a consuming application production-ready.

## Configure workload proof

Use one `DelegatedWorkloadTransport` for the selected workload revision and
attestation profile. In Kubernetes, the SDK reads the assertion only from the
fixed Kamiwaza projection path and keeps the P-256 proof key in process memory:

```python
from kamiwaza_sdk.delegated_workloads import (
    AttestationProfile,
    DelegatedControlPlaneClient,
    DelegatedExecutorClient,
    DelegatedWorkloadAPI,
    DelegatedWorkloadTransport,
    WorkloadProof,
)

proof = WorkloadProof.kubernetes(
    AttestationProfile.KUBERNETES_OFFLINE_V1,
)
transport = DelegatedWorkloadTransport(session, proof=proof)

base_url = "https://kamiwaza.example/api/v1/delegated-workloads"
control_plane = DelegatedControlPlaneClient(base_url, transport)
executor = DelegatedExecutorClient(base_url, transport)
workloads = DelegatedWorkloadAPI(base_url, transport)
```

The profile must be the current Core-selected profile for this registered
workload revision. Calling `transport.select_attestation_profile(...)` rotates
the proof key whenever the selection changes. Both Kubernetes v1 profiles read
the same projected assertion; the SDK never calls TokenReview or gains
Kubernetes API permission. Core alone decides whether the selected profile uses
offline verification or its platform-operated TokenReview adapter.

Do not read an arbitrary token path, persist the proof key, serialize the
assertion, or reuse transport authority after `transport.close()`. Assertions,
capabilities, DPoP proofs and nonces, broker handles, consumption tokens, and
CSRF material are redacted and reject pickling. This is a non-persistence and
non-exposure boundary; Python does not promise deterministic zeroization of
every prior heap copy.

## Member delegation stays in Core

Before reserving a run, the member must have consented on the Core-owned
surface to one immutable automation revision and its complete ceiling. The app
retains the returned grant ID and safe status only. It must not collect, proxy,
or store the member's consent decision, browser session, personal access token,
or refresh token.

The member remains the delegated subject and model-charge owner. The registered
client, workload role, revision, and attested instance remain the distinct
actor and safety-budget subjects. Supplying a grant ID never lets the workload
replace either principal or bypass Core's current-state checks.

## Reserve a run occurrence

`DelegatedControlPlaneClient.reserve_run` creates or recovers one idempotent
run occurrence. The request uses the control-plane workload assertion and a
fresh DPoP proof. It never accepts a member token or authority-bearing queue
payload.

```python
from uuid import UUID

from kamiwaza_sdk.delegated_workloads import (
    RunReservationRequest,
    RunTrigger,
)

reservation = control_plane.reserve_run(
    RunReservationRequest(
        grant_id=UUID("44444444-4444-4444-8444-444444444444"),
        revision_digest="sha256:" + "d" * 64,
        occurrence_key="scheduled:2026-08-09T12:00:00Z",
        trigger=RunTrigger.SCHEDULED,
    )
)
```

Omitting the authority argument is the normal path: the transport obtains fresh
assertion material through its selected adapter and creates an exact-request
DPoP proof. `WorkloadReadAuthority` remains a typed compatibility seam for
trusted custom adapters and tests; do not use it to make application code own
the projected token file.

The `occurrence_key` is the caller's stable idempotency key. Repeating the same
grant, revision digest, key, and trigger returns the existing run. Reusing the
key with a changed revision digest raises `OccurrenceDigestConflict`; the SDK
does not silently allocate a different run.

The response contains the Core run ID, correlation ID, immutable execution-
authority deadline, and an opaque transport reference. The reference is
redacted from model representations.

## Queue handoff

Queues transport location, never authority. Build the queue body only through
the typed handoff:

```python
queue.publish(reservation.queue_payload().model_dump(mode="json"))
```

The resulting object has exactly one field:

```json
{"run_reference":"opaque-value-returned-by-core"}
```

Do not add a member token, workload assertion, DPoP private key, run
capability, credential, subject ID, role, scope, or policy decision. An
executor that receives the reference must independently attest and atomically
claim the run through Core before it receives any run authority.

## Claim and execute a run

`DelegatedExecutorClient` binds the transport's public DPoP key while claiming
the opaque reference. Core returns a short-lived run capability and fencing
token only after workload attestation and the atomic claim succeed.

```python
from kamiwaza_sdk.delegated_workloads import (
    DelegatedExecutorClient,
    RunTransition,
    RunTransitionRequest,
)

executor = DelegatedExecutorClient(
    "https://kamiwaza.example/api/v1/delegated-workloads",
    transport,
)
claim = executor.claim_run(
    reservation.queue_payload(),
)
authority = executor.authority(claim)

executor.transition(
    RunTransitionRequest(transition=RunTransition.START),
    authority,
)
executor.transition(
    RunTransitionRequest(transition=RunTransition.HEARTBEAT),
    authority,
)
```

The client supplies the claim's fencing token; application code cannot replace
it with an unrelated value. Use `ACKNOWLEDGE_CANCEL` after observing a durable
cancellation request, and use `SUCCEED`, `FAIL`, `CANCEL`, or `AMBIGUOUS` for
the terminal outcome. A stale claim raises `FencedClaim` and must stop acting.

The durable lifecycle is `queued`, `claimed`, `running`,
`cancel_requested`, then exactly one of `succeeded`, `failed`, `cancelled`, or
`ambiguous`. Heartbeats extend the live claim lease only. They do not move the
run's immutable authority deadline, and a newer claimant's fence makes the old
executor unable to heartbeat, reserve effects, or report an outcome.

## Reserve an exact effect

Reserve every protected operation before application code performs it. The
request binds a stable effect key to the digest, action, resource, audience,
and optional destination or credential binding.

```python
from kamiwaza_sdk.delegated_workloads import (
    EffectReservationRequest,
    EffectResourceRef,
)

effect = executor.reserve_effect(
    EffectReservationRequest(
        effect_key="document:read",
        effect_digest="sha256:" + "e" * 64,
        action="read",
        resource=EffectResourceRef(
            type="example.document",
            descriptor_version="v1",
            id="doc-7",
        ),
        audience="https://documents.example",
    ),
    authority,
)
```

The effect call carries the DPoP-bound run capability but does not forward the
workload assertion to the protected resource. Reusing an effect key with a
different digest raises `EffectDigestConflict`. An allowed reservation is not
permission to bypass the guarded consumption step for the protected action.

Check the typed decision before dispatch. A `deny` is a normal fail-closed
decision, and `pending_approval` means the run must park without performing the
operation. An approval may later expose an opaque `resume_run_reference`; the
next executor must re-attest, claim that reference under a fresh fence, and
present the same effect key and digest. Approval never revives an expired run
capability.

## Resource enforcement and mesh parity

Workload code does not choose an Istio path or a direct path. It reserves the
same exact effect and calls the configured protected-resource endpoint. The
resource integration then uses the same Core decision and one-use consumption
contract in either topology:

1. `DelegatedWorkloadAPI.authorize_effect` obtains a side-effect-free typed
   allow or deny and, on allow, sealed dual-principal context plus a one-use
   consumption token.
2. `DelegatedWorkloadAPI.consume_effect` atomically consumes that token and
   starts the exact effect before protected application code runs.

With Istio, external authorization validates raw authority, strips spoofable
delegated headers, and installs only the contract's sealed headers. It does not
consume the effect, and its allow result is not final resource authorization.
Without Istio, the in-process guard performs the same decision and consumption
sequence without trusting forwarded identity. Application clients and resource
handlers must not parse capabilities, construct requester context, or add a
weaker local fallback.

The `DelegatedWorkloadAPI` authorization and consumption calls are
resource-integration primitives, not a way for an ordinary workload to
authorize itself. A protected handler runs only after the configured guard has
returned the consumed typed context.

## ReBAC and model quota behavior

Core evaluates the member's current ReBAC entitlement and the workload actor's
registration, role, revision, grant, run, claim, fence, and exact-effect ceiling
as one intersection. A valid member alone and a valid workload alone both
deny. The SDK does not accept member IDs, workload IDs, roles, ledger keys,
quota units, or an authority envelope from application input.

For delegated model calls, reserve an effect for the registered model resource
and let the protected model route derive the canonical request digest, model
target, and usage estimate. Before any provider or engine I/O, Core atomically
consumes the effect and reserves both ledgers:

- member and tenant charge; and
- client, workload revision, and run safety limits.

Either both reservations succeed or the model request is denied. Exact usage
settles both; positive evidence that work never started releases both; an
unknown stream, provider, or process outcome remains `ambiguous` for
reconciliation. Do not resubmit with caller-chosen quota values, a different
effect key, or a member-only model path after denial.

## Authority lifetime

The reservation's `authority_deadline` bounds one execution epoch. Individual
run capabilities remain shorter-lived and non-refreshable. A successor
requires fresh workload attestation and current grant, registration, proof,
claim, fencing, and policy state; neither a successor nor a heartbeat may move
the deadline.

Task definitions, checkpoints, history, approvals, and audit may remain
long-lived. Work that continues after the deadline resumes through a fresh run
and current consent rather than extending stale authority.

## Observe cancellation and preserve ambiguity

Read durable state with `workloads.get_run(claim.run_id)`, especially around
heartbeats and before starting another effect. When it reports
`RunLifecycleStatus.CANCEL_REQUESTED`, stop creating effects. Use
`RunTransition.ACKNOWLEDGE_CANCEL` only after you can prove the current work
stopped safely.

Cancellation does not turn an unknown external result into success or safe
non-execution. Report:

- `CANCEL` or `ACKNOWLEDGE_CANCEL` only when no protected operation may still
  have taken effect;
- `FAIL` for a deterministic terminal failure; and
- `AMBIGUOUS` when a provider call, mutation, stream, or lost response may have
  executed but its outcome cannot be proved.

`AMBIGUOUS` is terminal for automatic execution. Do not repeat the effect with
a new key, schedule a replacement run to redo it, or infer that an expired
lease means the operation did not happen. Use `workloads.get_effect(effect_id)`
and the correlation ID for authorized reconciliation; if evidence remains
insufficient, keep the ambiguous state explicit.

## Errors and safe retries

The transport maps the closed wire vocabulary to typed exceptions. Run
reservation is an idempotent protocol operation, so the transport may repeat
the exact request once when Core returns a DPoP nonce challenge. It never
replays application work to satisfy that challenge.

Treat these cases as terminal for the unchanged request:

- `OccurrenceDigestConflict`: choose a new occurrence key only for a genuinely
  different occurrence;
- `RevisionMismatch` or `GrantInactive`: obtain current member consent rather
  than weakening the request;
- `AttestationRejected` or `ProofMismatch`: refresh the permitted assertion or
  correct the proof identity; and
- `ReadinessUnavailable` or `IncompatibleContract`: do not dispatch until the
  complete required contract becomes ready.

`ClaimConflict` and `ApprovalRequired` are classified only for idempotent
read/state recovery. They are not permission to replay protected work.
`AmbiguousEffectOutcome` is never retryable. When the exact effect ID is known,
read its durable state instead of guessing from a lost response.

The transport may repeat the exact bytes of an explicitly idempotent protocol
request once after a valid DPoP nonce challenge. It creates a fresh proof and
`jti` for that retry. It never replays a model invocation, protected handler,
provider call, or mutation to satisfy the challenge.

No exception contains the workload assertion, opaque run reference, DPoP
private key, capability, or provider credential.

Close `transport` when the workload revision stops, the process shuts down, or
the proof lifecycle must be retired:

```python
transport.close()
```

After closure, proof creation fails locally with `ProofKeyUnavailable`. Rotate
or replace the proof lifecycle before new work; never fall back to bearer-only
requests.

See [Delegated credential broker](credentials.md) for member-safe binding
discovery and exact provider operations that never release provider secrets to
the workload.
