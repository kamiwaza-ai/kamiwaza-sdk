# Context Endpoint Coverage Matrix (updated Jun 12, 2026)

Routes are defined in `kamiwaza/services/context/api/*.py`. This matrix tracks
the subset of Context endpoints the SDK wraps — it is **not** the full server
surface (see "Not yet wrapped by the SDK" below).

| # | Method | Endpoint | SDK Method | Unit | Live | Assertion Strength |
|---|---|---|---|---|---|---|
| 1 | `GET` | `/context/health` | `health` | Y | Y | strict |
| 2 | `GET` | `/context/vectordbs` | `list_vectordbs` | Y | Y | strict |
| 3 | `GET` | `/context/vectordbs/{vectordb_id}` | `get_vectordb` | Y | Y | strict |
| 4 | `POST` | `/context/vectordbs` | `create_vectordb` | Y | Y | strict |
| 5 | `PUT` | `/context/vectordbs/{vectordb_id}` | `update_vectordb` | Y | Y | round-trip |
| 6 | `POST` | `/context/vectordbs/{vectordb_id}/scale` | `scale_vectordb` | Y | Y | round-trip |
| 7 | `DELETE` | `/context/vectordbs/{vectordb_id}` | `delete_vectordb` | Y | Y | strict |
| 8 | `POST` | `/context/vectordbs/{vectordb_id}/insert` | `insert_vectors` | Y | Y | strict |
| 9 | `POST` | `/context/vectordbs/insert` | `insert_vectors_global` | Y | Y | strict |
| 10 | `POST` | `/context/vectordbs/{vectordb_id}/query` | `query_vectors` | Y | Y | strict |
| 11 | `POST` | `/context/vectordbs/query` | `query_vectors_global` | Y | Y | strict |
| 12 | `GET` | `/context/ontologies` | `list_ontologies` | Y | Y | strict |
| 13 | `GET` | `/context/ontologies/{ontology_id}` | `get_ontology` | Y | Y | strict |
| 14 | `POST` | `/context/ontologies` | `create_ontology` | Y | Y | strict |
| 15 | `DELETE` | `/context/ontologies/{ontology_id}` | `delete_ontology` | Y | Y | strict |
| 16 | `POST` | `/context/ontologies/{ontology_id}/knowledge` | `add_knowledge` | Y | Y | strict |
| 17 | `POST` | `/context/ontologies/{ontology_id}/entity` | `add_entity` | Y | Y | strict |
| 18 | `POST` | `/context/ontologies/{ontology_id}/search` | `search_knowledge` | Y | Y | strict |
| 19 | `POST` | `/context/ontologies/{ontology_id}/memory` | `get_memory` | Y | Y | strict |
| 20 | `GET` | `/context/ontologies/{ontology_id}/episodes/{group_id}` | `get_episodes` | Y | Y | strict |
| 21 | `DELETE` | `/context/ontologies/{ontology_id}/groups/{group_id}` | `delete_group` | Y | Y | strict |
| 22 | `GET` | `/context/ontologies/{ontology_id}/health` | `ontology_health` | Y | Y | strict |
| 23 | `GET` | `/context/collections/` | `list_collections` | Y | Y | strict |
| 24 | `POST` | `/context/collections/` | `create_collection` | Y | Y | strict |
| 25 | `GET` | `/context/collections/{collection_name}` | `get_collection` | Y | Y | strict |
| 26 | `DELETE` | `/context/collections/{collection_name}` | `delete_collection` | Y | Y | strict |
| 27 | `POST` | `/context/pipelines/` | `create_pipeline_job` | Y | Y | strict |
| 28 | `GET` | `/context/pipelines/` | `list_pipeline_jobs` | Y | Y | strict |
| 29 | `GET` | `/context/pipelines/supported-types` | `get_supported_file_types` | Y | Y | strict |
| 30 | `GET` | `/context/pipelines/{job_id}` | `get_pipeline_job` | Y | Y | strict |
| 31 | `DELETE` | `/context/pipelines/{job_id}` | `delete_pipeline_job` | Y | Y | strict |
| 32 | `POST` | `/context/search` | `search` | Y | Y | strict |
| 33 | `POST` | `/context/retrieve` | `retrieve` | Y | Y | strict |
| 34 | `POST` | `/context/search/unified` | `agentic_search` | Y | Y | strict |
| 35 | `POST` | `/context/upload/` | `upload_file` | Y | Y | strict |
| 36 | `GET` | `/context/pipelines/import-options` | `get_import_options` | Y | Y | strict |
| 37 | `POST` | `/context/pipelines/import-options` | `evaluate_import_options` | Y | Y | strict |
| 38 | `POST` | `/context/pipelines/imports` | `create_source_import_job` | Y | deferred | strict |
| 39 | `GET` | `/context/pipelines/items` | `list_import_items` | Y | Y | strict |
| 40 | `POST` | `/context/pipelines/items/rerun` | `rerun_import_items` | Y | deferred | strict |
| 41 | `GET` | `/context/pipelines/{job_id}/items` | `list_pipeline_job_items` | Y | deferred | strict |
| 42 | `POST` | `/context/pipelines/{job_id}/retry` | `retry_pipeline_job` | Y | deferred | strict |
| 43 | `POST` | `/context/pipelines/{job_id}/rerun` | `rerun_pipeline_job` | Y | deferred | strict |
| 44 | `POST` | `/context/pipelines/{job_id}/cancel` | `cancel_pipeline_job` | Y | Y | strict |
| 45 | `POST` | `/context/storage/raw` | `store_raw_file` | Y | conditional | round-trip |
| 46 | `GET` | `/context/storage/raw` | `list_raw_files` | Y | conditional | strict |
| 47 | `GET` | `/context/storage/raw/{file_id}` | `get_raw_file` | Y | conditional | round-trip |
| 48 | `PUT` | `/context/storage/raw/{file_id}` | `update_raw_file` | Y | conditional | round-trip |

`agentic_search` wraps the canonical unified endpoint (`synthesize=True`). The
legacy `/context/search/agentic` route is deprecated server-side ("use POST
/search with synthesize=true instead"), so the SDK intentionally targets
`/context/search/unified` rather than the deprecated path the row originally
listed.

Rows 31 and 44 are deliberately distinct cancel semantics. `delete_pipeline_job`
(row 31, `DELETE /context/pipelines/{job_id}`) is the **destructive** verb — it
cancels a pending/running job and then removes the job **and** its recorded
history. `cancel_pipeline_job` (row 44, `POST /context/pipelines/{job_id}/cancel`)
is the **graceful** verb — it cancels the job while **preserving** its history.
The SDK previously bound `cancel_pipeline_job` to the destructive `DELETE`; that
name was repointed to the graceful route and the destructive path renamed to
`delete_pipeline_job` (a breaking rename).

Gap A live coverage (rows 38, 40–43) is **deferred**: source-import creation,
inventory rerun, and per-job retry/rerun replay all drive the OmniParse/Milvus
data plane, which the bare-core live host does not provision. These rows are
guarded by mocked unit tests; the live-debt is recorded in the PR body. The
import-options surface (rows 36–37), the workroom-wide inventory listing
(row 39), and the graceful cancel (row 44) are live-smokeable against bare core.

Raw-file object-storage rows (45–48) carry **conditional** live coverage: the
store→get→list→`If-Match` edit round-trip and the listing contract test run only
when the live host reports `workroom_object_storage` in `GET /context/health`
capabilities, and `skip` otherwise. A bare-core host without the S3/object-storage
data plane provisioned does not stand it up, so those rows fall back to the mocked
unit tests with the live-debt recorded in the PR body. The PUT edit asserts the
stale-`If-Match` 409 optimistic-concurrency contract.

`update_vectordb` and `scale_vectordb` (rows 5 & 6) carry **round-trip**
assertion strength rather than **strict**: the live tests confirm the SDK call
is accepted and the requested change is observable (the `update` config marker
round-trips via a follow-up `get_vectordb`; the `scale` response echoes the
requested `replicas`), but they do **not** assert physical replica provisioning.
Local Milvus is single-node, so a real replica scale may clamp or no-op — the
API round-trip is the meaningful, non-flaky contract these tests guard.

## Coverage Summary

These figures describe coverage **of the 48 wrapped routes above**, not of the
full Context server surface (see "Not yet wrapped by the SDK" for the unwrapped
remainder):

- Wrapped rows with an SDK method: **48/48**.
- Unit coverage of wrapped methods: **48/48**.
- Live coverage of wrapped methods: **35/48** — the 9 Gap A pipeline rows are unit-covered (rows 36, 37, 39, and 44 also live against bare core; rows 38 and 40–43 are **deferred**), and the 4 raw-file rows (45–48) carry **conditional** live coverage that runs only when the host reports `workroom_object_storage` and otherwise skips (see the notes above). All 48 are unit-covered.

## Not yet wrapped by the SDK

The 48 rows above are **not** the full Context surface — they are the subset the
SDK wraps today. The live `/context` router exposes **12** additional enumerated
user-facing routes — **3** intentional exclusions plus **9** real-gap routes
(verified against `kamiwaza/services/context/api/*.py`); the out-of-scope
`/embedding-model/*` family lives entirely outside this count. Each is triaged
below as either an **intentional exclusion** (with a one-line rationale) or a
**real gap** tracked by a follow-up ticket. Real gaps are **not** implemented in
this PR.

### Intentional exclusions (no SDK method, by design)

| Method | Endpoint | Rationale |
|---|---|---|
| `POST` | `/context/search/simple` | Deprecated server-side (`deprecated=True`, "Use POST /search instead"); the SDK wraps the canonical `/search` dispatcher (`search`), which routes legacy-shaped payloads to the same handler. |
| `POST` | `/context/search/agentic` | Deprecated server-side (`deprecated=True`, "Use POST /search with synthesize=true"); covered by `agentic_search` via `/context/search/unified`. |
| `POST` | `/context/search/retrieve` | Deprecated server-side (`deprecated=True`, "Use POST /search with format_context=true"); the SDK wraps the canonical `/retrieve` (`retrieve`, row 33). |
| (all) | `/context/embedding-model/*` | Out of scope per project decision (embedding-model lifecycle is managed outside the Context SDK surface). |

### Real gaps (tracked by follow-up tickets, not implemented here)

These routes are live, non-deprecated, and user-facing, but unwrapped. They are
grouped into coherent follow-up areas for later waves:

> **Gap A (Pipeline source-import / replay ops) is now wrapped** (rows 36–44
> above), including the breaking `cancel_pipeline_job` → `delete_pipeline_job`
> rename plus the new graceful `cancel_pipeline_job`. The remaining gaps below
> are renumbered accordingly.

**Gap A — Raw-file object storage CRUD** (`/context/storage/raw*`, 4 routes):

| Method | Endpoint | Note |
|---|---|---|
| `POST` | `/context/storage/raw` | Store a raw file into workroom-scoped object storage. |
| `GET` | `/context/storage/raw` | List raw files (filters, optional markings). |
| `GET` | `/context/storage/raw/{file_id}` | Get one raw-file record (optional presigned download URL). |
| `PUT` | `/context/storage/raw/{file_id}` | Edit plain-text raw-file content (If-Match concurrency). |

**Gap B — OmniParse instance lifecycle CRUD** (`/context/omniparses*`, 5 routes):

| Method | Endpoint | Note |
|---|---|---|
| `GET` | `/context/omniparses` | List OmniParse instances for the workroom. |
| `GET` | `/context/omniparses/{omniparse_id}` | Get one instance. |
| `POST` | `/context/omniparses` | Create a workroom-scoped instance. |
| `PUT` | `/context/omniparses/{omniparse_id}` | Update an instance. |
| `DELETE` | `/context/omniparses/{omniparse_id}` | Delete an instance. |

**Gap C — Misc workroom config / document retrieval** (`/context/*`, 4 routes):

| Method | Endpoint | Note |
|---|---|---|
| `GET` | `/context/global-settings` | Read global Context settings (ContextAdmin-scoped). |
| `PATCH` | `/context/global-settings` | Update global Context settings (ContextAdmin-scoped). |
| `GET` | `/context/documents/{source_urn}` | Presigned download URL for an original document by source URN. |
| `GET` | `/context/audio-readiness` | Workroom-scoped audio-readiness probe (OmniParse audio support). |

## Coverage scope note

This matrix tracks **44 wrapped routes** against a live Context surface of
**60 enumerated user-facing routes**: 44 wrapped + 3 intentional exclusions
(the deprecated `/search/*` legacy paths) + 13 real-gap routes across Gaps A–C
(4 + 5 + 4). The out-of-scope `/embedding-model/*` family is a wildcard
counted **outside** this 60 — it is not enumerated here. The "44/44" unit figure
in the Coverage Summary describes coverage **of the wrapped subset only**, not of
the full server surface — every unwrapped route is accounted for above.

## Next Actions

- File the Gap A–C follow-up tickets (one per area) to wrap the remaining real gaps in later waves.
