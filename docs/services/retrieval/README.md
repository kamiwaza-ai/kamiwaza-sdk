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

For datasets that exceed roughly 512 MiB, or when you explicitly request
`transport="grpc"` in your `RetrievalRequest`, the server returns a gRPC
Arrow Flight handshake instead of inline or SSE data.  The Flight path
requires the optional `flight` extra:

```bash
pip install "kamiwaza-sdk[flight]"
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

When the Kamiwaza client was constructed with `verify="/path/to/ca-bundle.pem"`
(or `ca_bundle=...`), that path is automatically forwarded to the Flight
transport so you don't have to pass it again.

### Mixed-version clusters

Older Kamiwaza server versions (pre-0.8) advertise `"protocol":
"kamiwaza.retrieval.v1"` in the Flight handshake rather than `"arrow-flight"`.
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

### Mid-stream failure semantics

Arrow Flight endpoints are *connection alternatives*, not resume points.  The
SDK retries each endpoint up to three times (with short backoffs) for
pre-stream failures such as connection refused or TLS handshake errors.  Once
at least one batch has been delivered, any subsequent error propagates
immediately — the SDK never falls back to another endpoint mid-stream, because
doing so would silently re-deliver data from offset 0.

If a mid-stream error occurs, the safe recovery path is to restart from job
creation:

```python
# Recovery pattern after a mid-stream Flight failure
job = client.retrieval.create_job(request)   # fresh job
for batch in client.retrieval.flight_batches(job):
    process(batch)
```
