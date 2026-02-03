# Enclaves Service

Client helpers live in `kamiwaza_sdk/services/enclaves.py` and wrap the
`/enclaves/connectors` and `/enclaves/documents` API surfaces.

## Usage

```python
from uuid import uuid4

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.schemas.enclaves import ConnectorCreate, IndexDocumentRequest

client = KamiwazaClient("https://localhost/api", api_key="...")

# Connectors
connector = client.enclaves.connectors.create(
    ConnectorCreate(
        name="demo-connector",
        source_type="s3",
        connector_type="s3",
        connection_config={"bucket": "demo"},
    )
)
connectors = client.enclaves.connectors.list(limit=10)
client.enclaves.connectors.trigger_ingest(connector.id)

# Documents
request = IndexDocumentRequest(
    source_id=connector.id,
    source_ref="s3://demo/report.txt",
    item_type="document",
    metadata={"title": "Report"},
)
client.enclaves.documents.create(request)

results = client.enclaves.documents.list(
    source_id=connector.id,
    limit=5,
    system_high="U",
)
print(results.total)
```

> **Routing note:** the FastAPI router is mounted under `/api/enclaves/*`, so the
> fully-qualified paths are `/enclaves/connectors` and `/enclaves/documents` when
> using a base URL that already includes `/api`.
