# Kamiwaza 0.7.0 Server Defects

Track integration issues observed while exercising catalog, ingestion, and retrieval flows via the SDK.

## 2025-11-07

### PAT bearer tokens rejected by `/auth/users/me` _(resolved 2025-11-10 via `aud=kamiwaza-platform`)_
```
POST /auth/pats
Authorization: Bearer <admin password grant token>

{"name": "sdk-m1", "ttl_seconds": 900, "scope": "openid", "aud": "kamiwaza-sdk"}
```

The API returned a token, but reusing it as `Authorization: Bearer <token>` resulted in:
```
GET /auth/users/me
Authorization: Bearer <pat token>

HTTP/1.1 401 Unauthorized
{"detail":"Not authenticated"}
```

Root cause: PATs must use the platform audience so the Keycloak validator recognizes them. Updating the integration test (and CLI helpers) to request PATs with `aud="kamiwaza-platform"` fixed the issue; `pytest -m integration -k pat` now passes.

## 2025-11-06

### Retrieval inline jobs return 500
```
POST /retrieval/retrieval/jobs HTTP/1.1
Authorization: Bearer <admin token>
Content-Type: application/json

{
  "dataset_urn": "urn:li:dataset:(urn:li:dataPlatform:s3,kamiwaza-sdk-tests//sdk-integration/visitors.parquet,PROD)",
  "transport": "inline",
  "format_hint": "parquet",
  "credential_override": "{\"aws_access_key_id\":\"minioadmin\",\"aws_secret_access_key\":\"minioadmin\",\"endpoint_override\":\"http://localhost:9100\",\"region\":\"us-east-1\"}"
}
```

Response:
```
HTTP/1.1 500 Internal Server Error
{"detail":"Internal Server Error"}
```

Observed after successfully ingesting the same dataset via `/ingestion/ingest/run`. Looks like the Ray-backed transport crashes in the server (no dataset/job entry returned).

### Ingestion router mounted as `/ingestion/ingest`
Spec/`070-update.md` list endpoints as `/ingest/*`, but the FastAPI router is included under `/ingestion`, so the deployed path is `/ingestion/ingest/run`. Probably a docs fix (the service originated as standalone).

The earlier `location` mismatch defect still applies; ingestion writes `properties.path`, but retrieval requires `properties.location`. When patches add `location` manually the inline job still fails with the 500 above.

- **S3 dataset URNs include duplicate slash** — _resolved via 2025‑11‑xx server build_  
  Path/value: previously `kamiwaza-sdk-tests//sdk-integration/visitors.parquet`, now `kamiwaza-sdk-tests/sdk-integration/visitors.parquet` per `pytest -m integration` run on macOS.  
  Repro (historical): run the docker-backed integration test (`tests/integration/test_catalog_ingest_retrieval.py`) to ingest the sample parquet object. The ingest response returned a dataset URN whose S3 component contained a `//` between the bucket and key.  
  Result: dataset metadata (and downstream retrieval requests) carried the redundant slash, pointing at `s3://kamiwaza-sdk-tests//sdk-integration/visitors.parquet`. Root cause fixed server-side; SDK formatter no longer emits the malformed URN, so this item can fall off the open defect list unless it regresses.

- **Retrieval jobs fail with HTTP 500**  
  Path: `POST /retrieval/retrieval/jobs`  
  Repro: ingest S3 parquet dataset via `/ingestion/ingest/run`, then request inline retrieval with explicit credentials.  
  Result: API returns 500 `Internal Server Error` even after ensuring dataset properties include `endpoint`, `region`, and `path`. Ray-backed transport appears to crash server-side.

- **Retrieval rejects datasets missing `properties.location`**  
  Path: `POST /retrieval/retrieval/jobs`  
  Repro: ingest via S3 plugin (which stores `properties.path`), then attempt retrieval.  
  Result: API responds 400 `"Dataset is missing a location property"`. Retrieval service expects `properties["location"]`, but ingestion writes `path`. Manual PATCH adding `location` works around it.

- **Ingestion API mounted at `/ingestion/ingest/*` not `/ingest/*`**  
  Documentation/spec mention `/ingest`. Actual FastAPI router includes `/ingestion` prefix, so client must call `/ingestion/ingest/run`. Update spec or provide alias.
