# Context Service

## Overview
The Context Service (`ContextService`, accessed via `client.context`) is the SDK
wrapper for the Kamiwaza Context Service. Located in
`kamiwaza_sdk/services/context.py`, it manages workroom-scoped vector databases,
ontologies (knowledge graphs), ingestion pipelines, and retrieval against the
documents and knowledge held inside a **Workroom**.

Every Context Service resource lives inside exactly one workroom. The SDK targets
a workroom by passing `workroom_id` to a method (sent to the server as the
`X-Workroom-ID` header). Some methods make `workroom_id` a **required**
keyword-only argument (e.g. `list_collections`, `create_pipeline_job`, `search`,
`retrieve`, `upload_file`); others accept it optionally, and when it is omitted
the server resolves the caller's default workroom.

## Workrooms and the Global Workroom

A **Workroom** is the collaboration and isolation boundary for context: vector
collections, ontologies, ingested files, and pipeline jobs all belong to a
workroom, and access is scoped to that workroom's members.

The **Global Workroom** is a special, well-known workroom (the all-`f` sentinel
`ffffffff-ffff-ffff-ffff-ffffffffffff`, exposed as
`ContextService.DEFAULT_WORKROOM_ID`). It holds the platform's **shared,
read-only catalog** of context — knowledge that is visible to everyone but owned
by no single member.

### The Global Workroom is read-only for tenant writes

> **Key semantics:** Any direct write that targets the Global Workroom is
> rejected by the server with **HTTP 403** and a body of
> `Global Workroom is read-only for <operation>` (for example
> `Global Workroom is read-only for ontology creation`). This is **intentional
> and by design**, not a bug — the Global Workroom is a shared catalog, so it is
> populated only by the platform's own ingestion paths, never by ad-hoc tenant
> writes.

**Reads against the Global Workroom are always allowed** (results are
requester-scoped where appropriate). Writes are blocked. The table below lists
the **server-side policy categories** — some (e.g. pipeline retry/rerun/delete,
workroom archive/restore/purge) are enforced by the Context Service but are not
all surfaced as `client.context` methods; the SDK exposes a subset (see the
method lists below).

| Operation category | Against a normal workroom | Against the Global Workroom |
|--------------------|---------------------------|------------------------------|
| List / get / query / search / retrieve (reads) | ✅ allowed | ✅ allowed |
| VectorDB create / update / delete / insert | ✅ allowed | ❌ 403 read-only |
| Ontology create / delete / add_knowledge / add_entity / delete_group | ✅ allowed | ❌ 403 read-only |
| Collection create / delete, chunk indexing / deletion | ✅ allowed | ❌ 403 read-only |
| Pipeline job create / cancel / retry / rerun / delete | ✅ allowed | ❌ 403 read-only |
| Raw file upload | ✅ allowed | ❌ 403 read-only |
| Workroom archive / restore / purge (lifecycle) | ✅ allowed | ❌ 403 read-only |

> **Note on `*_global` helpers.** `query_vectors_global()` is a **read** against
> the shared catalog and works for any caller. `insert_vectors_global()` targets
> the same shared catalog and is reserved for the platform's connector-runtime
> ingestion path — a direct tenant call will hit the same 403 read-only policy.
> To store your own vectors, create or use a normal (non-global) workroom.

If you are migrating tests or code that assumed the Global Workroom was writable:
point the write at a workroom you own (pass its `workroom_id`), and only read
from the Global Workroom.

## VectorDB

### Available Methods
- `list_vectordbs(*, workroom_id=None)`
- `get_vectordb(vectordb_id, *, workroom_id=None)`
- `create_vectordb(*, workroom_id=None, ...)`
- `update_vectordb(vectordb_id, *, workroom_id=None, ...)`
- `scale_vectordb(vectordb_id, *, workroom_id=None, ...)`
- `delete_vectordb(vectordb_id, *, workroom_id=None)`
- `query_vectors(vectordb_id, *, collection_name, vectors, limit=10, workroom_id=None)`
- `query_vectors_global(*, vectordb_id, collection_name, vectors, limit=10)` — read the shared catalog
- `insert_vectors(vectordb_id, *, collection_name, vectors, metadata, workroom_id=None)`
- `insert_vectors_global(...)` — connector-runtime catalog writes only (see note above)

```python
# db_id, embedding, and my_workroom_id are values you supply.

# Read from the shared Global catalog (allowed)
hits = client.context.query_vectors_global(
    vectordb_id=db_id,
    collection_name="shared-docs",
    vectors=[embedding],
    limit=5,
)

# Write to a workroom you own (allowed) — NOT the Global Workroom
client.context.insert_vectors(
    db_id,
    collection_name="my-docs",
    vectors=[embedding],
    metadata=[{"source": "doc1"}],
    workroom_id=my_workroom_id,
)
```

## Ontology (Knowledge Graph)

### Available Methods
- `list_ontologies(*, workroom_id=None)` / `get_ontology(...)`
- `create_ontology(...)` / `delete_ontology(...)`
- `add_knowledge(...)` / `add_entity(...)` / `delete_group(...)`
- `search_knowledge(...)` / `get_memory(...)` / `get_episodes(...)`
- `ontology_health(...)`

Reads (`list_*`, `get_*`, `search_knowledge`, `get_memory`, `get_episodes`,
`ontology_health`) work against any workroom including Global. Writes
(`create_ontology`, `delete_ontology`, `add_knowledge`, `add_entity`,
`delete_group`) are rejected on the Global Workroom.

## Collections, Pipelines, and Retrieval

### Available Methods
- `list_collections(...)` / `create_collection(...)` / `get_collection(...)` / `delete_collection(...)`
- `create_pipeline_job(...)` / `list_pipeline_jobs(...)` / `get_pipeline_job(...)`
- `get_supported_file_types()`
- `search(...)` / `retrieve(...)` / `upload_file(...)`

#### Pipeline cancel vs. delete

The SDK exposes two distinct teardown verbs for a pipeline job:

- `cancel_pipeline_job(...)` — **graceful** cancel
  (`POST /context/pipelines/{job_id}/cancel`). Stops the job but **preserves**
  its recorded history; the job stays fetchable via `get_pipeline_job(...)`.
- `delete_pipeline_job(...)` — **destructive** delete
  (`DELETE /context/pipelines/{job_id}`). Cancels a pending/running job and then
  removes the job **and** its history.

> **Breaking change:** `cancel_pipeline_job(...)` previously mapped to the
> destructive `DELETE` route. It now maps to the graceful cancel route, and the
> destructive behavior moved to the new `delete_pipeline_job(...)`. Update any
> caller that relied on the old `cancel_pipeline_job` to hard-delete a job.

#### Provider-neutral source import and replay

For Kaizen / import-shell automation, the SDK wraps the provider-neutral
source-import and replay surface:

- `get_import_options(...)` / `evaluate_import_options(...)` — aggregate and
  validate import options for selected source descriptors.
- `create_source_import_job(...)` — start a provider-neutral source-import job.
- `list_import_items(...)` — workroom-wide source-import inventory/history.
- `rerun_import_items(...)` — rerun selected inventory items by recorded source
  descriptor.
- `list_pipeline_job_items(...)` — per-item statuses for one job.
- `retry_pipeline_job(...)` — retry failed/incomplete items from a replayable
  import job.
- `rerun_pipeline_job(...)` — rerun all recorded source descriptors from a prior
  import job.

Collection/pipeline/file **writes** follow the same rule: allowed in a normal
workroom, `403` on the Global Workroom.

## Error Handling

```python
from kamiwaza_sdk.exceptions import KamiwazaError

# my_workroom_id is a workroom you own. The Global Workroom id is available as
# client.context.DEFAULT_WORKROOM_ID.
try:
    client.context.create_ontology(
        name="my-graph",
        backend="graphiti",
        workroom_id=client.context.DEFAULT_WORKROOM_ID,  # Global → will 403
    )
except KamiwazaError as exc:
    if exc.status_code == 403 and "read-only" in str(exc):
        # Expected: the Global Workroom is a shared, read-only catalog.
        # Retry the write against a workroom you own instead.
        client.context.create_ontology(
            name="my-graph",
            backend="graphiti",
            workroom_id=my_workroom_id,
        )
    else:
        raise
```

## Best Practices
1. Treat the Global Workroom as **read-only**: query/search it, but write your
   own resources into a workroom you own.
2. Pass an explicit `workroom_id` on writes so a resource never accidentally
   resolves to the Global Workroom (an omitted workroom can resolve to Global on
   some paths and will be rejected).
3. Branch on `status_code == 403` + `"read-only"` to distinguish the shared-catalog
   policy from a genuine permission error.
4. Use `query_vectors_global()` / read-only ontology queries to consume shared
   knowledge; never rely on writing to Global from tenant code.
