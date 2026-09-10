# Neutral delegated-workload client

This example is a workload client, not an extension. It uses only the public
delegated-workload SDK and HTTP contract. It has no extension manifest,
operator dependency, Tomo dependency, member token, registrar credential, or
application-owned token path.

An authorized registrar must first register separate control-plane and executor
roles, their immutable workload revision, and the acceptable attestation
profiles. Registration is deliberately outside this workload process: a client
cannot register itself or widen its own authority.

## Configure the client

Run control-plane and executor roles as separate workloads. Each role gets the
fixed Kamiwaza projected assertion at
`/var/run/secrets/kamiwaza.ai/workload-identity/token`; the SDK checks that path
and file boundary and owns the ephemeral P-256 proof key.

```python
import requests

from examples.delegated_workload_client import (
    NeutralClientConfig,
    NeutralWorkloadClient,
)
from kamiwaza_sdk.delegated_workloads import AttestationProfile

config = NeutralClientConfig(
    base_url="https://kamiwaza.example/api/v1/delegated-workloads",
    profile=AttestationProfile.KUBERNETES_OFFLINE_V1,
)
client = NeutralWorkloadClient(config, requests.Session())
```

Use the profile selected for the current registered workload revision. The
portable offline profile requires no Kubernetes API permission. Do not copy a
projected assertion into an environment variable, `.env` file, queue, log, or
database.

## Control-plane handoff

The control-plane process reserves one idempotent occurrence from a durable
member grant:

```python
from uuid import UUID

from examples.delegated_workload_client import queue_message
from kamiwaza_sdk.delegated_workloads import RunReservationRequest, RunTrigger

reservation = client.reserve_run(
    RunReservationRequest(
        grant_id=UUID("44444444-4444-4444-8444-444444444444"),
        revision_digest="sha256:" + "d" * 64,
        occurrence_key="scheduled:2026-08-09T12:00:00Z",
        trigger=RunTrigger.SCHEDULED,
    )
)
queue.publish(queue_message(reservation))
```

The queue message has exactly one opaque field, `run_reference`. It contains no
capability, assertion, member identity, role, scope, proof key, credential, or
policy decision.

## Executor lifecycle

The executor independently attests, binds its current proof key, and atomically
claims the opaque message before Core returns a short-lived capability and
fencing token. Those values remain inside `NeutralClaim` and its SDK clients.

```python
from kamiwaza_sdk.delegated_workloads import (
    EffectReservationRequest,
    EffectResourceRef,
    RunTransition,
)

claim = client.claim_run(queue.receive())
claim.transition(RunTransition.START)

effect = claim.reserve_effect(
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
    )
)
```

Dispatch only an `allow` result through the registered protected-resource
guard. A `deny` is terminal for that request. A `pending_approval` result parks
without running the operation; resumption requires a new opaque queue
reference, fresh attestation, a new fenced claim, and the same effect key and
digest.

Heartbeat while work is active. Respect durable cancellation and report one
terminal state:

```python
claim.transition(RunTransition.HEARTBEAT)
claim.transition(RunTransition.SUCCEED, "completed")
```

A stale fence, capability failure, unknown response, or current denial stops
work. If a mutation may have reached the resource but its outcome is unknown,
report `RunTransition.AMBIGUOUS`; never replay it automatically. Call
`client.close()` (or use the client as a context manager) when the process ends
so the proof lifecycle cannot be reused.

The runnable conformance resource and full cross-client journey are separate
examples. See [`../../docs/delegated-workloads/protocol.md`](../../docs/delegated-workloads/protocol.md)
for raw HTTP, DPoP, error, compatibility, and protected-resource rules.
