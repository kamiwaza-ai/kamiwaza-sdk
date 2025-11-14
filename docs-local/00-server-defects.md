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

### Retrieval inline jobs return 500 {#retrieval-inline-jobs-return-500}
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

**Status – 2025-11-12 (“Retrieval Inline Fix”)**  
- Upstream change (`kamiwaza/services/retrieval/adapters/s3.py`) now honors per-request overrides for `endpoint`, `endpoint_override`, and `region`, preventing the adapter from falling back to stale catalog metadata and crashing Ray when pointed at MinIO or other non-default endpoints.  
- Regression coverage: `kamiwaza/services/retrieval/tests/test_s3_adapter.py` (mocks `pyarrow.fs.S3FileSystem` to assert overrides win) plus the SDK integration tests `test_catalog_inline_small_object_succeeds`, `test_catalog_inline_large_object_hits_threshold`, `test_catalog_large_object_sse_retrieval`, and `test_s3_ingest_and_retrieve_inline`. These verify <500 KB payloads stay inline, ~1.3 MB payloads raise the documented 422 threshold error, and SSE fallback still streams rows.  
- Local stack expected to return 201/422/200 sequences per above; any regression will now surface as a hard test failure (no `xfail`).

### Ingestion router mounted as `/ingestion/ingest`
Spec/`070-update.md` list endpoints as `/ingest/*`, but the FastAPI router is included under `/ingestion`, so the deployed path is `/ingestion/ingest/run`. Probably a docs fix (the service originated as standalone).

The earlier `location` mismatch defect still applies; ingestion writes `properties.path`, but retrieval requires `properties.location`. When patches add `location` manually the inline job still fails with the 500 above.

- **S3 dataset URNs include duplicate slash** — _resolved via 2025‑11‑xx server build_  
  Path/value: previously `kamiwaza-sdk-tests//sdk-integration/visitors.parquet`, now `kamiwaza-sdk-tests/sdk-integration/visitors.parquet` per `pytest -m integration` run on macOS.  
  Repro (historical): run the docker-backed integration test (`tests/integration/test_catalog_ingest_retrieval.py`) to ingest the sample parquet object. The ingest response returned a dataset URN whose S3 component contained a `//` between the bucket and key.  
  Result: dataset metadata (and downstream retrieval requests) carried the redundant slash, pointing at `s3://kamiwaza-sdk-tests//sdk-integration/visitors.parquet`. Root cause fixed server-side; SDK formatter no longer emits the malformed URN, so this item can fall off the open defect list unless it regresses.

- **Retrieval rejects datasets missing `properties.location`**  
  Path: `POST /retrieval/retrieval/jobs`  
  Repro: ingest via S3 plugin (which stores `properties.path`), then attempt retrieval.  
  Result: API responds 400 `"Dataset is missing a location property"`. Retrieval service expects `properties["location"]`, but ingestion writes `path`. Manual PATCH adding `location` works around it.

### Ingestion router mounted as `/ingestion/ingest` _(resolved 2025-11-12)_
Spec `kamiwaza-openapi-spec.json` already reflects the `/ingestion/ingest/*` prefix, so the doc drift noted earlier has been cleared. Any lingering references to `/ingest/*` in docs should be updated; the SDK now consistently calls `/ingestion/ingest/run` and friends.

## 2025-11-08

### File ingest path constraints _(resolved 2025-11-14)_ {#file-ingest-path-constraints}
```
pytest tests/integration/test_catalog_multi_source.py -k file_ingestion_metadata
```
Recent backend change whitelisted `tests/integration/catalog_stack/state/test-data`, so the File ingester now accepts the path we exercise in CI without the "Path outside allowed directories" error. Keep an eye on regressions if the allowlist changes again.

### File retrieval missing {#file-retrieval-missing}
Even when the File ingester succeeds (manually seeded inside `/data`), the retrieval service refuses to materialize the dataset because it requires object-store metadata (`endpoint`, `location`) that file datasets do not have. Track the gap so we can either implement a local file transport or document the limitation.

### Object JSON retrieval {#object-json-retrieval}
Ingesting `objects/sample.json` via the S3 plugin succeeds, but calling `/retrieval/retrieval/jobs` with `format_hint="json"` returns 422 "Unsupported transport". Repro: `pytest tests/integration/test_catalog_multi_source.py::test_catalog_object_ingestion_inline_retrieval`. Retrieving Parquet blobs is supposed to work once the inline fix rolls out broadly, so this entry tracks the non-tabular JSON gap specifically.



### Kafka retrieval missing {#kafka-retrieval-missing}
Kafka ingestion populates catalog containers/topics, but `/retrieval/retrieval/jobs` can't materialize topic metadata or events (`pytest tests/integration/test_catalog_multi_source.py::test_catalog_kafka_ingestion_metadata`). Until we have a streaming transport, keep the SDK test marked xfail to flag regressions.

### Slack retrieval missing {#slack-retrieval-missing}
Slack ingestion (when provided with `SLACK_TEST_TOKEN` + channel/team IDs) only writes metadata/documentation and cannot be replayed through retrieval. The backend should either expose the collected messages via retrieval or document that Slack datasets are catalog-only.
