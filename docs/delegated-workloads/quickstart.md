# Delegated workload quickstart

This walkthrough connects a registered workload client to a separately
deployed protected resource without a member token, long-lived platform
credential, extension manifest, or application-owned authority model.

Use the Python SDK examples for the shortest path. A non-Python client can use
the same [HTTP protocol and conformance fixture](protocol.md).

## Prerequisites

Before application deployment, a platform operator must provide:

- an active workload client and immutable revision;
- separate control-plane and executor roles with selected attestation
  profiles;
- an active protected-resource descriptor revision and guard contract;
- a member-approved grant for the exact automation revision, resources, and
  actions; and
- discovery showing the complete required v1 capability set compatible and
  healthy.

Do not continue when discovery is incomplete. A successful test read does not
open a partial production mode.

## 1. Configure workload proof

Mount the platform-projected assertion at the fixed SDK path:

```text
/var/run/secrets/kamiwaza.ai/workload-identity/token
```

The portable Kubernetes profile requires no Kubernetes API permission. Create
one proof lifecycle per registered role and process. The two roles are shown
together only to make the handoff visible; deploy them as distinct workloads:

```python
import requests

from kamiwaza_sdk.delegated_workloads import (
    AttestationProfile,
    DelegatedWorkloadClient,
    DelegatedWorkloadTransport,
    WorkloadProof,
)

base_url = "https://kamiwaza.example/api/v1/delegated-workloads"
control_proof = WorkloadProof.kubernetes(
    AttestationProfile.KUBERNETES_OFFLINE_V1,
)
control_transport = DelegatedWorkloadTransport(
    requests.Session(),
    proof=control_proof,
)
control_plane = DelegatedWorkloadClient(
    base_url,
    control_transport,
).control_plane()

executor_proof = WorkloadProof.kubernetes(
    AttestationProfile.KUBERNETES_OFFLINE_V1,
)
executor_transport = DelegatedWorkloadTransport(
    requests.Session(),
    proof=executor_proof,
)
executor = DelegatedWorkloadClient(base_url, executor_transport).executor()
```

The SDK reads fresh assertion material, creates an in-memory P-256 proof key,
and binds every authority-bearing request with DPoP. Never copy the assertion
or proof key to an environment variable, queue, log, database, or checkpoint.

## 2. Reserve an occurrence

The control-plane role reserves an idempotent occurrence against the durable
member grant:

```python
from uuid import UUID

from kamiwaza_sdk.delegated_workloads import RunReservationRequest, RunTrigger

reservation = control_plane.reserve_run(
    RunReservationRequest(
        grant_id=UUID("44444444-4444-4444-8444-444444444444"),
        revision_digest="sha256:" + "d" * 64,
        occurrence_key="scheduled:2026-08-09T12:00:00Z",
        trigger=RunTrigger.SCHEDULED,
    )
)
queue.publish(reservation.queue_payload().model_dump(mode="json"))
```

The queue body contains exactly one opaque `run_reference`. Queue possession
does not authorize execution.

## 3. Claim and start

The executor independently attests and atomically claims the reference:

```python
from kamiwaza_sdk.delegated_workloads import (
    OpaqueRunQueuePayload,
    RunTransition,
    RunTransitionRequest,
)

queue_payload = OpaqueRunQueuePayload.model_validate(queue.receive())
claim = executor.claim_run(queue_payload)
authority = executor.authority(claim)
executor.transition(
    RunTransitionRequest(transition=RunTransition.START),
    authority,
)
```

Keep the returned `NeutralClaim` in process memory. It contains a short-lived
run capability and monotonic fencing token. A stale or losing claimant must
stop immediately.

## 4. Reserve an exact resource effect

Describe the exact registered action, resource, audience, and stable
idempotency digest:

```python
from kamiwaza_sdk.delegated_workloads import (
    EffectReservationRequest,
    EffectResourceRef,
)

effect_request = EffectReservationRequest(
    effect_key="document:read",
    effect_digest="sha256:" + "e" * 64,
    action="read",
    resource=EffectResourceRef(
        type="conformance.document",
        descriptor_version="v1",
        id="doc-7",
    ),
    audience="https://documents.example.test",
)
effect = executor.reserve_effect(effect_request, authority)
```

Dispatch only when `effect.decision` is `allow` and an effect capability is
present. `deny` stops the operation. `pending_approval` parks the run without
calling the resource.

For a mutation, an authorized member-session approver decides the exact effect
digest and current policy version through Core. The workload never receives
the member session or CSRF secret. Resume only through Core's opaque reference,
fresh attestation, a current claim/fence, and the same effect key and digest.

## 5. Call the guarded resource

Send the effect capability, a fresh exact-request DPoP proof, and the current
workload assertion to the configured resource endpoint. The public transport
owns proof construction; do not hand-build bearer headers or requester
identity.

The resource guard verifies locally, obtains a current Core decision, consumes
the one-use token, and installs a sealed context before its handler. A second
call with consumed authority is denied before application code.

The neutral resource exposes:

- `GET https://documents.example.test/v1/documents/<id>` for `read`; and
- `PUT https://documents.example.test/v1/documents/<id>` for exact-approved
  `mutate` with `{"title":"..."}`.

For the read above, send the exact empty body with SDK-owned proof material:

```python
from kamiwaza_sdk.delegated_workloads import (
    DelegatedProtocolRequest,
    ProtocolRetrySafety,
)

if effect.effect_capability is None:
    raise RuntimeError("effect was not authorized")

response = executor_transport.send_json(
    DelegatedProtocolRequest(
        method="GET",
        url="https://documents.example.test/v1/documents/doc-7",
        body=b"",
        capability=effect.effect_capability,
        extra_headers=((
            "X-Kamiwaza-Workload-Assertion",
            executor_transport.workload_assertion(),
        ),),
        retry_safety=ProtocolRetrySafety.IDEMPOTENT_PROTOCOL,
    )
)
```

See [protected resource integration](resources.md) for the deployable ASGI
example and guard wiring. Workload application code should use its normal HTTP
client with the SDK-managed proof material; it must not call Core's guard
decision endpoint to authorize itself.

## 6. Finish safely

Heartbeat only while the current claim is active, observe durable
cancellation, and report exactly one terminal result:

```python
executor.transition(
    RunTransitionRequest(transition=RunTransition.HEARTBEAT),
    authority,
)
executor.transition(
    RunTransitionRequest(
        transition=RunTransition.SUCCEED,
        outcome_category="completed",
    ),
    authority,
)
executor_transport.close()
control_transport.close()
```

Use `FAIL` for deterministic failure, `CANCEL` only when work is proven
stopped, and `AMBIGUOUS` whenever a mutation or external call may have executed
but its result is unknown. Never replay an ambiguous effect automatically.

## Verify the integration

From the SDK repository root, run:

```bash
uv run pytest -q tests/contract/delegated_workloads/test_http_protocol_vectors.py
uv run pytest -q tests/contract/delegated_workloads/test_neutral_resource_server.py
uv run pytest -q tests/e2e/delegated_workloads/test_new_resource_onboarding.py
uv run pytest -q tests/e2e/delegated_workloads/test_protocol_parity.py
```

The onboarding journey proves staged descriptor activation, run claim,
pending mutation, exact approval, P-256/DPoP verification, current decision,
one-use consumption, sealed attribution, readback, replay denial, and terminal
run completion. The parity suite runs the same published success and error
vectors through the Python SDK and a dependency-free raw HTTP client.

Before production rollout, also confirm:

- Core discovery reports every mandatory v1 family healthy and compatible;
- the active workload/resource revisions and selected profile match deployed
  artifacts and configuration;
- the resource has no unguarded data route or self-registration credential;
- queues, logs, traces, errors, prompts, and persistence contain no assertion,
  capability, proof, nonce, consumption token, credential, or member session;
- cancellation, ambiguity, key rotation, revocation, rollout, rollback, audit,
  and dependency-loss tests pass in the target environment; and
- the consuming application keeps its rollout gate closed until all required
  conformance and live evidence is current.

Read [registrars](registrars.md) before implementing installation wiring and
[delegated workload client](client.md) for the complete lifecycle and retry
contract.
