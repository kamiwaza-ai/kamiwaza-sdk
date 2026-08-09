# Delegated workload client

The delegated-workload package exposes protocol clients for registered
workloads and protected resources. A workload acts on behalf of a member; it
does not become a member session. Core derives the member subject from durable
consent and keeps the registered workload or agent as a distinct actor.

> This surface is gated by delegated-workload discovery and the complete v1
> readiness contract. A successful internal read or run reservation does not
> by itself make a consuming application production-ready.

## Reserve a run occurrence

`DelegatedControlPlaneClient.reserve_run` creates or recovers one idempotent
run occurrence. The request uses the control-plane workload assertion and a
fresh DPoP proof. It never accepts a member token or authority-bearing queue
payload.

```python
from uuid import UUID

from kamiwaza_sdk.delegated_workloads import (
    DelegatedControlPlaneClient,
    DelegatedWorkloadTransport,
    RunReservationRequest,
    RunTrigger,
    WorkloadReadAuthority,
)

transport = DelegatedWorkloadTransport(session)
control_plane = DelegatedControlPlaneClient(
    "https://kamiwaza.example/api/v1/delegated-workloads",
    transport,
)

reservation = control_plane.reserve_run(
    RunReservationRequest(
        grant_id=UUID("44444444-4444-4444-8444-444444444444"),
        revision_digest="sha256:" + "d" * 64,
        occurrence_key="scheduled:2026-08-09T12:00:00Z",
        trigger=RunTrigger.SCHEDULED,
    ),
    WorkloadReadAuthority(
        workload_assertion=projected_workload_assertion,
    ),
)
```

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

## Authority lifetime

The reservation's `authority_deadline` bounds one execution epoch. Individual
run capabilities remain shorter-lived and non-refreshable. A successor
requires fresh workload attestation and current grant, registration, proof,
claim, fencing, and policy state; neither a successor nor a heartbeat may move
the deadline.

Task definitions, checkpoints, history, approvals, and audit may remain
long-lived. Work that continues after the deadline resumes through a fresh run
and current consent rather than extending stale authority.

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

No exception contains the workload assertion, opaque run reference, DPoP
private key, capability, or provider credential.
