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
| 49 | `GET` | `/context/omniparses` | `list_omniparses` | Y | Y | strict |
| 50 | `GET` | `/context/omniparses/{omniparse_id}` | `get_omniparse` | Y | deferred | round-trip |
| 51 | `POST` | `/context/omniparses` | `create_omniparse` | Y | deferred | round-trip |
| 52 | `PUT` | `/context/omniparses/{omniparse_id}` | `update_omniparse` | Y | deferred | round-trip |
| 53 | `DELETE` | `/context/omniparses/{omniparse_id}` | `delete_omniparse` | Y | deferred | round-trip |
| 54 | `GET` | `/context/global-settings` | `get_global_settings` | Y | Y | strict |
| 55 | `PATCH` | `/context/global-settings` | `update_global_settings` | Y | Y | round-trip |
| 56 | `GET` | `/context/documents/{source_urn}` | `get_document_download_url` | Y | deferred | strict |
| 57 | `GET` | `/context/audio-readiness` | `get_audio_readiness` | Y | Y | strict |

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

OmniParse instance-lifecycle rows (49–53): the list route (row 49) is
metadata-only and live-smokeable against bare core (a fresh workroom lists no
instances). The create/get/update/delete CRUD (rows 50–53) provision an
OmniParse runtime via App Garden (tool template + container images), which is
**data-plane-heavy and not available on the bare-core TRCM box**, so their live
coverage is **deferred**: the `create → get → update → delete` round-trip test
attempts a create and `skip`s when the data plane is unprovisioned. These rows
fall back to mocked unit tests and the live-debt is recorded in the PR body.

`update_vectordb` and `scale_vectordb` (rows 5 & 6) carry **round-trip**
assertion strength rather than **strict**: the live tests confirm the SDK call
is accepted and the requested change is observable (the `update` config marker
round-trips via a follow-up `get_vectordb`; the `scale` response echoes the
requested `replicas`), but they do **not** assert physical replica provisioning.
Local Milvus is single-node, so a real replica scale may clamp or no-op — the
API round-trip is the meaningful, non-flaky contract these tests guard.

## Coverage Summary

These figures describe coverage **of the 57 wrapped routes above**, not of the
full Context server surface (see "Not yet wrapped by the SDK" for the unwrapped
remainder):

- Wrapped rows with an SDK method: **57/57**.
- Unit coverage of wrapped methods: **57/57**.
- Live coverage of wrapped methods: **39/57** — the 9 Gap A pipeline rows are unit-covered (rows 36, 37, 39, and 44 also live against bare core; rows 38 and 40–43 are **deferred**), the 4 raw-file rows (45–48) carry **conditional** live coverage that runs only when the host reports `workroom_object_storage` and otherwise skips, the 5 OmniParse rows (49–53) cover the list route live against bare core (row 49) with the create/get/update/delete CRUD (rows 50–53) **deferred** to mocked unit tests pending the App Garden data plane, and of the 4 Gap C config/document rows (54–57) the global-settings GET/PATCH (54–55) and audio-readiness (57) live-smoke against bare core while document download (56) is **deferred** pending a stored document + S3 (see the notes above). All 57 are unit-covered.

## Not yet wrapped by the SDK

The 57 rows above are **not** the full Context surface — they are the subset the
SDK wraps today. The live `/context` router exposes **3** additional enumerated
user-facing routes — all **intentional exclusions** (the deprecated `/search/*`
legacy paths), verified against `kamiwaza/services/context/api/*.py`; the
out-of-scope `/embedding-model/*` family lives entirely outside this count. Each
is triaged below as an **intentional exclusion** (with a one-line rationale). No
unwrapped real-gap routes remain.

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

> **Gap B (OmniParse instance lifecycle CRUD) is now wrapped** (rows 49–53
> above): `list_omniparses` / `get_omniparse` / `create_omniparse` /
> `update_omniparse` / `delete_omniparse`. The list route is live-smokeable
> against bare core; the create/get/update/delete CRUD live coverage is
> **deferred** pending the App Garden OmniParse data plane (mocked-unit covered).
> The remaining gap below keeps its letter for continuity.

> **Gap C (Misc workroom config / document retrieval) is now wrapped** (rows
> 54–57 above): `get_global_settings` / `update_global_settings` (platform-scoped
> ContextAdmin config, not workroom-scoped), `get_document_download_url`, and
> `get_audio_readiness`. global-settings GET/PATCH and audio-readiness are
> live-smokeable against bare core; the document-download happy path needs a
> stored document plus S3 object storage (un-provisioned data plane), so its live
> coverage is **deferred** (mocked-unit covered, with a live wiring-check for the
> 404/501 path).

## Coverage scope note

This matrix tracks **57 wrapped routes** against a live Context surface of
**60 enumerated user-facing routes**: 57 wrapped + 3 intentional exclusions
(the deprecated `/search/*` legacy paths). The out-of-scope `/embedding-model/*`
family is a wildcard counted **outside** this 60 — it is not enumerated here. The
"57/57" unit figure in the Coverage Summary describes coverage **of the wrapped
subset only**, not of the full server surface — every unwrapped route is
accounted for above.

## Next Actions

- Wrap the remaining Gap C real gaps (misc workroom config / document retrieval) in a later wave.
