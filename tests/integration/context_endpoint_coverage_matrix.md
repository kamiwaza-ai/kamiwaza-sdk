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

- Rows with an SDK method: **35/35**.
- Unit coverage of wrapped methods: **35/35**.
- Live coverage of wrapped methods: **35/35** — `update_vectordb` and `scale_vectordb` live coverage was restored via workroom-scoped round-trip tests (the `session_workroom` + `shared_workroom_vectordb` fixtures), closing the gap left by the per-session-workroom refactor.

## Not yet wrapped by the SDK

These live `/context` routes have no SDK method and are not tracked above. Triage
each for intentional exclusion vs. a real gap before treating coverage as complete:

- Search: `/search/simple`; `/search/agentic` and `/search/retrieve` are deprecated server-side (the SDK wraps the canonical `/search` + `/search/unified` paths instead)
- Pipelines: `/pipelines/import-options` (GET/POST), `/pipelines/imports`, `/pipelines/items`, `/pipelines/items/rerun`, `/pipelines/{job_id}/items`, `/pipelines/{job_id}/retry`, `/pipelines/{job_id}/rerun`, `POST /pipelines/{job_id}/cancel`
- Other: `/global-settings`, `/omniparses` CRUD, `/storage/*`, `/documents/{source_urn}`, audio-readiness
- Embedding-model routes (`/embedding-model`) are intentionally out of scope.

## Next Actions

- Triage the "Not yet wrapped" routes so the tracked denominator reflects the real surface.
