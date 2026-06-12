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
| 5 | `PUT` | `/context/vectordbs/{vectordb_id}` | `update_vectordb` | Y | N | unit-only |
| 6 | `POST` | `/context/vectordbs/{vectordb_id}/scale` | `scale_vectordb` | Y | N | unit-only |
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

## Coverage Summary

- Rows with an SDK method: **35/35**.
- Unit coverage of wrapped methods: **35/35**.
- Live coverage of wrapped methods: **33/35** — `update_vectordb` and `scale_vectordb` are unit-only (live coverage was dropped during the per-session-workroom refactor and not restored).

## Not yet wrapped by the SDK

These live `/context` routes have no SDK method and are not tracked above. Triage
each for intentional exclusion vs. a real gap before treating coverage as complete:

- Search: `/search/simple`; `/search/agentic` and `/search/retrieve` are deprecated server-side (the SDK wraps the canonical `/search` + `/search/unified` paths instead)
- Pipelines: `/pipelines/import-options` (GET/POST), `/pipelines/imports`, `/pipelines/items`, `/pipelines/items/rerun`, `/pipelines/{job_id}/items`, `/pipelines/{job_id}/retry`, `/pipelines/{job_id}/rerun`, `POST /pipelines/{job_id}/cancel`
- Other: `/global-settings`, `/omniparses` CRUD, `/storage/*`, `/documents/{source_urn}`, audio-readiness
- Embedding-model routes (`/embedding-model`) are intentionally out of scope.

## Next Actions

- Restore live coverage for `update_vectordb` / `scale_vectordb`, or accept unit-only and document why.
- Triage the "Not yet wrapped" routes so the tracked denominator reflects the real surface.
