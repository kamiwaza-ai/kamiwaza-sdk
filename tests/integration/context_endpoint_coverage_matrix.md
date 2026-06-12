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
| 31 | `DELETE` | `/context/pipelines/{job_id}` | `cancel_pipeline_job` | Y | Y | strict |
| 32 | `POST` | `/context/search` | `search` | Y | Y | strict |
| 33 | `POST` | `/context/retrieve` | `retrieve` | Y | Y | strict |
| 34 | `POST` | `/context/search/unified` | `agentic_search` | Y | Y | strict |
| 35 | `POST` | `/context/upload/` | `upload_file` | Y | Y | strict |

`agentic_search` wraps the canonical unified endpoint (`synthesize=True`). The
legacy `/context/search/agentic` route is deprecated server-side ("use POST
/search with synthesize=true instead"), so the SDK intentionally targets
`/context/search/unified` rather than the deprecated path the row originally
listed.

`update_vectordb` and `scale_vectordb` (rows 5 & 6) carry **round-trip**
assertion strength rather than **strict**: the live tests confirm the SDK call
is accepted and the requested change is observable (the `update` config marker
round-trips via a follow-up `get_vectordb`; the `scale` response echoes the
requested `replicas`), but they do **not** assert physical replica provisioning.
Local Milvus is single-node, so a real replica scale may clamp or no-op — the
API round-trip is the meaningful, non-flaky contract these tests guard.

## Coverage Summary

These figures describe coverage **of the 35 wrapped routes above**, not of the
full Context server surface (see "Not yet wrapped by the SDK" for the unwrapped
remainder):

- Wrapped rows with an SDK method: **35/35**.
- Unit coverage of wrapped methods: **35/35**.
- Live coverage of wrapped methods: **35/35** — `update_vectordb` and `scale_vectordb` live coverage was restored via workroom-scoped round-trip tests (the `session_workroom` + `shared_workroom_vectordb` fixtures), closing the gap left by the per-session-workroom refactor.

## Not yet wrapped by the SDK

The 35 rows above are **not** the full Context surface — they are the subset the
SDK wraps today. The live `/context` router exposes ~22 additional user-facing
routes (verified against `kamiwaza/services/context/api/*.py`). Each is triaged
below as either an **intentional exclusion** (with a one-line rationale) or a
**real gap** tracked by a follow-up ticket. Real gaps are **not** implemented in
this PR.

### Intentional exclusions (no SDK method, by design)

| Method | Endpoint | Rationale |
|---|---|---|
| `POST` | `/search/simple` | Deprecated server-side (`deprecated=True`, "Use POST /search instead"); the SDK wraps the canonical `/search` dispatcher (`search`), which routes legacy-shaped payloads to the same handler. |
| `POST` | `/search/agentic` | Deprecated server-side (`deprecated=True`, "Use POST /search with synthesize=true"); covered by `agentic_search` via `/search/unified`. |
| `POST` | `/search/retrieve` | Deprecated server-side (`deprecated=True`, "Use POST /search with format_context=true"); the SDK wraps the canonical `/retrieve` (`retrieve`, row 33). |
| (all) | `/embedding-model/*` | Out of scope per project decision (embedding-model lifecycle is managed outside the Context SDK surface). |

### Real gaps (tracked by follow-up tickets, not implemented here)

These routes are live, non-deprecated, and user-facing, but unwrapped. They are
grouped into coherent follow-up areas for later waves:

**Gap A — Pipeline source-import / replay ops** (`/pipelines/*`):

| Method | Endpoint | Note |
|---|---|---|
| `GET` | `/pipelines/import-options` | Aggregated provider-neutral import options. |
| `POST` | `/pipelines/import-options` | Evaluate selected source descriptors against import rules. |
| `POST` | `/pipelines/imports` | Create a provider-neutral source-import job. |
| `GET` | `/pipelines/items` | Workroom-wide source-import inventory/history. |
| `POST` | `/pipelines/items/rerun` | Rerun selected inventory items by recorded source descriptor. |
| `GET` | `/pipelines/{job_id}/items` | Per-item statuses for one job. |
| `POST` | `/pipelines/{job_id}/retry` | Retry failed/incomplete items from a replayable import job. |
| `POST` | `/pipelines/{job_id}/rerun` | Rerun all recorded source descriptors from a prior import job. |
| `POST` | `/pipelines/{job_id}/cancel` | **Graceful cancel that preserves job history.** Distinct from the SDK's `cancel_pipeline_job` (row 31), which maps to `DELETE /pipelines/{job_id}` — a hard delete that cancels *and removes* the job + its recorded history. The non-destructive cancel verb is currently unreachable from the SDK. |

**Gap B — Raw-file object storage CRUD** (`/storage/raw*`, 4 routes):

| Method | Endpoint | Note |
|---|---|---|
| `POST` | `/storage/raw` | Store a raw file into workroom-scoped object storage. |
| `GET` | `/storage/raw` | List raw files (filters, optional markings). |
| `GET` | `/storage/raw/{file_id}` | Get one raw-file record (optional presigned download URL). |
| `PUT` | `/storage/raw/{file_id}` | Edit plain-text raw-file content (If-Match concurrency). |

**Gap C — OmniParse instance lifecycle CRUD** (`/omniparses*`, 5 routes):

| Method | Endpoint | Note |
|---|---|---|
| `GET` | `/omniparses` | List OmniParse instances for the workroom. |
| `GET` | `/omniparses/{omniparse_id}` | Get one instance. |
| `POST` | `/omniparses` | Create a workroom-scoped instance. |
| `PUT` | `/omniparses/{omniparse_id}` | Update an instance. |
| `DELETE` | `/omniparses/{omniparse_id}` | Delete an instance. |

**Gap D — Misc workroom config / document retrieval**:

| Method | Endpoint | Note |
|---|---|---|
| `GET` | `/global-settings` | Read global Context settings (ContextAdmin-scoped). |
| `PATCH` | `/global-settings` | Update global Context settings (ContextAdmin-scoped). |
| `GET` | `/documents/{source_urn}` | Presigned download URL for an original document by source URN. |
| `GET` | `/audio-readiness` | Workroom-scoped audio-readiness probe (OmniParse audio support). |

## Coverage scope note

This matrix tracks **35 wrapped routes** against a live Context surface of roughly
**57 user-facing routes** (35 wrapped + 4 intentional exclusions + ~18 real-gap
routes across Gaps A–D, plus the out-of-scope `/embedding-model/*` family). The
"35/35" figures in the Coverage Summary describe coverage **of the wrapped
subset only**, not of the full server surface — every unwrapped route is
accounted for above.

## Next Actions

- File the Gap A–D follow-up tickets (one per area) to wrap the real gaps in later waves.
