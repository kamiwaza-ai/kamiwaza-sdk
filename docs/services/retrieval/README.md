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
