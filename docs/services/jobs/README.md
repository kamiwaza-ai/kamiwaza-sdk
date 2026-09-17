# Federated Jobs Service

`client.jobs` submits local or federated Ray jobs. Existing callers may keep
using `run`, `submit_async`, `wait`, and `cancel` without delegated access.

## Governed delegated access

Use `DelegatedAccess` to name exact receiver resources and operations. The SDK
validates the same closed vocabulary as Core before making a request. Wildcards,
duplicates, unknown operations, and an empty access block are rejected.

```python
from kamiwaza_sdk import (
    DatasetDelegatedAccess,
    DelegatedAccess,
    ModelDelegatedAccess,
)

job_id = client.jobs.submit_async(
    target_cluster="receiver",
    entrypoint="python summarize.py",
    delegated_access=DelegatedAccess(
        datasets=(
            DatasetDelegatedAccess(
                urn=DATASET_URN,
                operations=("discover", "read", "retrieve"),
            ),
        ),
        models=(
            ModelDelegatedAccess(
                deployment_id=DEPLOYMENT_ID,
                operations=("discover", "chat"),
            ),
        ),
    ),
)
```

Passing a mapping with the same shape remains supported. Omitting
`delegated_access` preserves the ordinary job wire format and execution path.

### Approved Python dependencies

An isolated delegated job can request exact Python packages from the receiver
operator's approved catalog:

```python
job_id = client.jobs.submit_async(
    target_cluster="receiver",
    entrypoint="python summarize.py",
    delegated_access=access,
    python_packages=["humanize==4.13.0"],
)
```

Only exact `name==version` coordinates are accepted. URLs, version ranges,
extras, markers, duplicate package names, and more than 32 packages are rejected
before the request is sent. The receiver resolves each coordinate to its
operator-approved SHA-256 hashes and installs it from the configured private
Python index. Repository URLs, credentials, and trust material are never part
of the SDK request or the Ray job environment. Air-gapped deployments can point
the operator-managed repository Secret at an internal PEP 503/691-compatible
mirror without changing job code.

The older `pip=` convenience parameter belongs to ordinary Ray runtime
environments. Use `python_packages=` for isolated delegated jobs.

## In-job runtime client

Managed job code uses `JobRuntimeClient`, which connects only to the private
Unix socket created for that job:

```python
from kamiwaza_sdk import JobRuntimeClient

with JobRuntimeClient.from_environment() as receiver:
    datasets = receiver.datasets.list_granted()
    models = receiver.models.list_granted()
    rows = receiver.retrieval.collect(dataset_urn=DATASET_URN)
    answer = receiver.models.chat(
        deployment_id=DEPLOYMENT_ID,
        messages=[{"role": "user", "content": summarize(rows)}],
    )
```

Streaming variants are `retrieval.stream` and `models.stream_chat`. Each
operation re-reads the platform-owned agent identity and verifies the connected
Unix peer before sending a request, so a restarted agent is used safely on the
next operation. The client never falls back to a bearer token, password, API
key, refresh token, or public HTTP endpoint.

Denied resources raise `DelegatedResourceNotFoundError`; lifecycle revocation
raises `DelegationRevokedError`; attestation failure raises
`JobIdentityUnavailableError`; grant denial raises
`DelegatedOperationDeniedError`; and authority, dependency, or replay-store
outages raise `DelegationUnavailableError`.
