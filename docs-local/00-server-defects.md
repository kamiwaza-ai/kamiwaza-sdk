# Kamiwaza 0.7.0 Server Defects

Track integration issues observed while exercising catalog, ingestion, and retrieval flows via the SDK.

## 2026-06-17

### Catalog container list endpoint returns 500

`GET /catalog/containers/` can return HTTP 500 on the current live stack, even
for the admin-gated setup probe used by the SDK live catalog endpoint tests.
`tests/integration/test_catalog_endpoints.py` skips the affected container
coverage when this exact 500 is observed so non-container catalog regressions
remain visible.

### Live S3 ingest endpoint unreachable from platform

The SDK live ingestion fixture can stage S3-compatible test data, but the
platform-side ingest worker can fail to connect to the configured object-store
endpoint and return HTTP 500 with `Could not connect to the endpoint URL`.
`tests/integration/test_catalog_ingest_retrieval.py` skips only that exact
transport failure; other ingestion API failures still propagate.

### Graphiti ontology readiness/provisioning does not become ready

Context ontology provisioning can remain non-ready long enough to exhaust the
SDK live test readiness timeout. `tests/integration/test_context_live.py` skips
timeout-only readiness stalls after deleting the temporary ontology, while
terminal failed/stopped ontology states remain hard failures.

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
POST /retrieval/jobs HTTP/1.1
Authorization: Bearer <admin token>
Content-Type: application/json

{
  "dataset_urn": "urn:li:dataset:(urn:li:dataPlatform:s3,kamiwaza-sdk-tests//sdk-integration/visitors.parquet,PROD)",
  "transport": "inline",
  "format_hint": "parquet",
  "credential_override": "{\"aws_access_key_id\":\"minioadmin\",\"aws_secret_access_key\":\"minioadmin\",\"endpoint_override\":\"http://localhost:19100\",\"region\":\"us-east-1\"}"
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
  Path: `POST /retrieval/jobs`  
  Repro: ingest via S3 plugin (which stores `properties.path`), then attempt retrieval.  
  Result: API responds 400 `"Dataset is missing a location property"`. Retrieval service expects `properties["location"]`, but ingestion writes `path`. Manual PATCH adding `location` works around it.  
  **Status – 2025-11-16**: Server now backfills `properties.location` directly. Removed the SDK-side patching in `tests/integration/test_catalog_ingest_retrieval.py` + `test_catalog_multi_source.py` and reran `pytest tests/integration/test_catalog_ingest_retrieval.py::test_s3_ingest_and_retrieve_inline` twice with clean passes.

### Retrieval gRPC transport fails {#retrieval-grpc-transport-fails}
```
POST /retrieval/jobs HTTP/1.1
Content-Type: application/json

{
  "dataset_urn": "urn:li:dataset:(urn:li:dataPlatform:s3,kamiwaza-sdk-tests/sdk-integration/visitors.parquet,PROD)",
  "transport": "grpc",
  "format_hint": "parquet",
  "credential_override": "{\"aws_access_key_id\":\"minioadmin\",\"aws_secret_access_key\":\"minioadmin\",\"endpoint_override\":\"http://localhost:19100\",\"region\":\"us-east-1\"}"
}
```

Response:
```
HTTP/1.1 500 Internal Server Error
{"detail":"Internal Server Error"}
```

The same dataset/materialisation succeeds via inline transport, but requesting `transport="grpc"` never returns a handshake. `tests/integration/test_catalog_ingest_retrieval.py::test_s3_ingest_and_retrieve_grpc` is marked `pytest.skip` until the server can establish a gRPC job. Tracked as `INC-006` in `docs-local/03-sdk-inconsistencies.md`.

### Ingestion router mounted as `/ingestion/ingest` _(resolved 2025-11-12)_
Spec `kamiwaza-openapi-spec.json` already reflects the `/ingestion/ingest/*` prefix, so the doc drift noted earlier has been cleared. Any lingering references to `/ingest/*` in docs should be updated; the SDK now consistently calls `/ingestion/ingest/run` and friends.

## 2025-11-08

### File ingest path constraints _(resolved 2025-11-14)_ {#file-ingest-path-constraints}
```
pytest tests/integration/test_catalog_multi_source.py -k file_ingestion_metadata
```
Recent backend change whitelisted `tests/integration/catalog_stack/state/test-data`, so the File ingester now accepts the path we exercise in CI without the "Path outside allowed directories" error. Keep an eye on regressions if the allowlist changes again.

### File retrieval missing _(resolved 2025-11-14)_ {#file-retrieval-missing}
File ingests that point at `tests/integration/catalog_stack/state/test-data` can return inline payloads via `/retrieval/jobs` once the backend sets `RETRIEVAL_FILESYSTEM_ALLOWED_ROOTS` to include that path. Regression covered by `tests/integration/test_catalog_multi_source.py::test_catalog_file_ingestion_metadata`, which ingests the sample tree and asserts `row_count >= 1` from the inline job response (skips when the server has filesystem retrieval disabled).

### Object JSON retrieval {#object-json-retrieval}
Ingesting `objects/sample.json` via the S3 plugin succeeds, but calling `/retrieval/jobs` with `format_hint="json"` returns 422 "Unsupported transport". Repro: `pytest tests/integration/test_catalog_multi_source.py::test_catalog_object_ingestion_inline_retrieval`. Retrieving Parquet blobs is supposed to work once the inline fix rolls out broadly, so this entry tracks the non-tabular JSON gap specifically.



### Kafka retrieval missing {#kafka-retrieval-missing}
Kafka ingestion populates catalog containers/topics, but `/retrieval/jobs` can't materialize topic metadata or events (`pytest tests/integration/test_catalog_multi_source.py::test_catalog_kafka_ingestion_metadata`). Until we have a streaming transport, keep the SDK test marked xfail to flag regressions.

### Slack retrieval missing _(resolved 2025-11-14)_ {#slack-retrieval-missing}
Slack ingestion can now stream conversations (and optional replies) via the retrieval API when supplied with a bot token. Regression coverage: `tests/integration/test_catalog_multi_source.py::test_catalog_slack_ingestion_metadata` ingests a channel and asserts that `/retrieval/jobs` returns inline rows when `SLACK_TEST_TOKEN`/`SLACK_TEST_CHANNEL`/`SLACK_TEST_TEAM` env vars are provided.

## 2026-07-01 - SDK skip reenable pass {#sdk-skip-reenable-20260701}

Branch: `chore/reenable-tests`.

### Harness skips converted to coverage
- Write-scope PAT tests now bootstrap from the session admin PAT instead of independently re-probing env/password auth. Repro: `uv run pytest -m integration -q tests/integration/test_write_scope_access.py --tb=short`; result: `8 passed`.
- Catalog stack generated data now defaults under `tests/integration/catalog_stack/state/generated-data` instead of the root-owned compose fixture directory. Repro: `uv run pytest -m integration -q tests/integration/test_catalog_multi_source.py -k 'not slack' --tb=short`; result: `7 passed, 1 deselected, 2 xfailed`.
- Llama.cpp matrix no longer inherits the MLX deployability marker, uses an available GGUF quantization (`q4_k`), and lets the matrix helper own deployment waiting with `deploy_model(wait=False)`.

### Open findings from the same run
- **External auth path denies valid tokens.** `/api/auth/token` returns a valid admin Keycloak bearer for `admin`, but `/api/auth/users/me`, `/api/whoami`, `/api/models`, and `/api/auth/pats` all return empty `403` responses from `istio-envoy` after about five seconds. A local PAT signed with the platform local key validates inside the core process, but the same external routes return the same empty `403`. The admin user row was also found in the wrong shape (`external_id` set to the Keycloak subject, `linked_subject_id` empty); moving that subject to `linked_subject_id` and clearing `external_id` did not clear the external 403s. Auth-dependent reenable buckets are blocked until this is fixed.
- **PAT workroom enter is not the automation contract.** Core now treats PAT-backed `workrooms.enter` as selected-session mutation and rejects it with `409 binding_invalid`. SDK automation should use explicit request scope via `client.workroom_scope(workroom_id)` / `scoped_client_for_workroom(...)`; `tests/integration/test_seeding_live.py` now covers that path separately from the PAT-enter negative contract.
- **Slack ingest regressed despite provided env.** `tests/integration/test_catalog_multi_source.py::test_catalog_slack_ingestion_metadata` fails with `500` and detail `Unexpected ingestion failure: Event loop is closed` while the Slack plugin creates the DataHub secret. Slack `auth.test` succeeds; `team.info` also reports missing `team:read`, which is a secondary token-scope issue rather than the 500 root cause.
- **Llama.cpp deploy path has platform blockers.** After the harness gate/quant fixes, deploy reached the live platform but the host requested `kamiwaza_gpus: 1.0` on a no-GPU/UMA setup, and Ray autoscaler reported no node type could satisfy it. Core logs also showed GGUF download failures under `/app/models/unsloth/...` with permission denied. The deployment status read was also affected by the external auth `403` blocker above.
- **Catalog file/Kafka remain expected xfails.** File ingestion metadata still returns no retrievable datasets in this topology, and Kafka still exposes metadata without a retrieval transport.
