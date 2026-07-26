# Retrieval Service

The `kamiwaza_sdk.services.retrieval.RetrievalService` module drives the
job-oriented retrieval API introduced in Kamiwaza 0.7.0.

## Creating a job

```python
from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.schemas.retrieval import RetrievalRequest

client = KamiwazaClient("https://localhost/api", api_key="...")

request = RetrievalRequest(
    dataset_urn="urn:li:dataset:(urn:li:dataPlatform:s3,my-bucket/my-key,PROD)",
    transport="inline",
    format_hint="parquet",
    credential_override='{"aws_access_key_id":"...","aws_secret_access_key":"..."}',
)
job = client.retrieval.create_job(request)
if job.inline:
    print("Rows:", job.inline.data)
else:
    print("Transport:", job.transport)
```

## Polling status

```python
status = client.retrieval.get_job(job.job_id)
print(status.status, status.progress)
```

## Streaming output

For SSE jobs (`transport="sse"`), `stream_job` yields raw server-sent-event lines:

```python
for event in client.retrieval.stream_job(job.job_id):
    print(event)
```

> **Routing note:** the router is mounted under `/retrieval`, so the live paths
> are `/retrieval/jobs`, `/retrieval/jobs/{job_id}`, etc.

## Flight transport (large datasets)

When the server selects Flight, or when you explicitly request
`transport="grpc"` in your `RetrievalRequest`, it returns a gRPC Arrow Flight
handshake instead of inline or SSE data. The Flight path requires the optional
`flight` extra:

```bash
pip install "kamiwaza-sdk[flight]>=1.1.0"
```

### Consuming Flight batches

```python
import pyarrow as pa
from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.schemas.retrieval import RetrievalRequest

client = KamiwazaClient("https://kamiwaza.example/api", api_key="...")

# 1. Create a job — the server selects grpc automatically for large data.
request = RetrievalRequest(
    dataset_urn="urn:li:dataset:(urn:li:dataPlatform:s3,my-bucket/big-file.parquet,PROD)",
    transport="grpc",
)
job = client.retrieval.create_job(request)

# 2. Stream batches over Arrow Flight.
#    Pass ca_cert_path for self-signed or private CA environments.
batches = []
for batch in client.retrieval.flight_batches(job, ca_cert_path="/path/to/ca-bundle.pem"):
    batches.append(batch)

table = pa.Table.from_batches(batches)
print(table.to_pandas())
```

Production and E2E servers advertise `grpc+tls://` endpoints. TLS is required
by default. When the Kamiwaza client was constructed with
`verify="/path/to/ca-bundle.pem"` (or `ca_bundle=...`), that path is
automatically forwarded to Flight. Empty CA data is rejected. Supplying a CA
for a plaintext URI is also rejected because gRPC ignores TLS roots on
plaintext transports.

Plaintext is available only as an explicit local-development escape hatch:

```python
for batch in client.retrieval.flight_batches(job, allow_insecure=True):
    process(batch)
```

`flight_batches` has a finite one-hour deadline for the complete `DoGet` and
stream read, including retry backoffs and endpoint fallback, plus gRPC
keepalives. Override the deadline when needed:

```python
for batch in client.retrieval.flight_batches(job, timeout_seconds=7200):
    process(batch)
```

A deadline failure raises the typed `FlightTimeoutError` and is never retried.

### Mixed-version clusters

Legacy Kamiwaza servers can advertise `"protocol":
"kamiwaza.retrieval.v1"` in the Flight handshake rather than `"arrow-flight"`.
An omitted discriminator also defaults to that legacy protocol; Flight
therefore requires an explicit server advertisement of `"arrow-flight"`.
Calling `flight_batches` against such a server raises
`TransportNotSupportedError` with a clear message naming the unsupported
protocol, instead of producing a confusing connection error:

```python
from kamiwaza_sdk.exceptions import TransportNotSupportedError

try:
    for batch in client.retrieval.flight_batches(job):
        ...
except TransportNotSupportedError as exc:
    print(f"Server is too old for Flight: {exc}")
    # Fall back: recreate the job with transport="sse" or transport="inline"
```

### Retry, token, and completion semantics

Arrow Flight endpoints are *connection alternatives*, not resume points.  The
server atomically consumes the single-use handshake token when a `DoGet` is
claimed, and does not offer an API for refreshing the handshake per endpoint.
The SDK retries an endpoint and falls back only after PyArrow reports a typed
`FlightUnavailableError` before any batch has been delivered. It never retries
timeouts, server errors, authentication or authorization failures, arbitrary
exceptions, or any error after the first batch.

Because the token is consumed when the server claims `DoGet`, retry can only
succeed when unavailability occurs before that claim. If an unavailable
attempt claimed the token and the next attempt rejects it, the SDK preserves
the original typed `FlightUnavailableError` instead of misreporting the
secondary rejection as an authentication failure.

If a mid-stream error occurs, the safe recovery path is to restart from job
creation:

```python
# Recovery pattern after a mid-stream Flight failure
job = client.retrieval.create_job(request)   # fresh job
for batch in client.retrieval.flight_batches(job):
    process(batch)
```

The server reports `total_records=-1`, so the SDK does not invent a row-count
integrity check. Instead, after a natural clean stream exhaustion,
`flight_batches` reads the job status and requires `COMPLETED`. A clean EOF
with any other status—or an unavailable status check—raises
`FlightIncompleteStreamError`. If a caller intentionally closes or abandons
the iterator early, completion is intentionally not checked.
