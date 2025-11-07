# Ingestion Service

Client helpers live in `kamiwaza_sdk/services/ingestion.py` and wrap the
`/ingestion/ingest` API surface exposed by Kamiwaza.

## Usage

```python
from kamiwaza_sdk import KamiwazaClient

client = KamiwazaClient("https://localhost/api", api_key="...")  # or set authenticator

# Active ingestion (runs immediately)
resp = client.ingestion.run_active(
    "s3",
    bucket="kamiwaza-sdk-tests",
    prefix="sdk-integration",
    endpoint_url="http://localhost:9100",
    region="us-east-1",
    aws_access_key_id="minioadmin",
    aws_secret_access_key="minioadmin",
)
print(resp.urns)

# Emit a Metadata Change Proposal
client.ingestion.emit_mcp({"entityType": "dataset", "changeType": "UPSERT", "entityUrn": resp.urns[0]})

# Schedule recurring jobs
from kamiwaza_sdk.schemas.ingestion import IngestJobCreate

job = IngestJobCreate(
    job_id="nightly-s3-sync",
    schedule="0 3 * * *",
    source_type="s3",
    conn_args={"bucket": "...", "recursive": True},
)
client.ingestion.schedule_job(job)
status = client.ingestion.get_job_status(job.job_id)
print(status.status, status.last_run)
```

> **Routing note:** the FastAPI router is included under `/ingestion`, so the
> fully-qualified paths are `/ingestion/ingest/run`, `/ingestion/ingest/jobs`,
> etc.
