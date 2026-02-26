# Context Endpoint Coverage Matrix (Feb 26, 2026)

Source of truth routes: `kamiwaza/services/context/api/*.py` (35 endpoints).

| # | Method | Endpoint | SDK Method | Unit | Live | Assertion Strength |
|---|---|---|---|---|---|---|
| 1 | `GET` | `/context/health` | `health` | Y | Y | strict |
| 2 | `GET` | `/context/vectordbs` | `list_vectordbs` | Y | Y | strict |
| 3 | `GET` | `/context/vectordbs/{vectordb_id}` | `get_vectordb` | Y | Y | strict |
| 4 | `POST` | `/context/vectordbs` | `create_vectordb` | Y | Y | strict |
| 5 | `PUT` | `/context/vectordbs/{vectordb_id}` | `update_vectordb` | Y | Y | strict |
| 6 | `POST` | `/context/vectordbs/{vectordb_id}/scale` | `scale_vectordb` | Y | Y | strict |
| 7 | `DELETE` | `/context/vectordbs/{vectordb_id}` | `delete_vectordb` | Y | Y | strict |
| 8 | `POST` | `/context/vectordbs/{vectordb_id}/insert` | `insert_vectors` | Y | Y | blocked-xfail (`D260-004`) |
| 9 | `POST` | `/context/vectordbs/insert` | `insert_vectors_global` | Y | Y | blocked-xfail (`D260-004`) |
| 10 | `POST` | `/context/vectordbs/{vectordb_id}/query` | `query_vectors` | Y | Y | blocked-xfail (`D260-004`) |
| 11 | `POST` | `/context/vectordbs/query` | `query_vectors_global` | Y | Y | blocked-xfail (`D260-004`) |
| 12 | `GET` | `/context/ontologies` | `list_ontologies` | Y | Y | strict |
| 13 | `GET` | `/context/ontologies/{ontology_id}` | `get_ontology` | Y | Y | strict |
| 14 | `POST` | `/context/ontologies` | `create_ontology` | Y | Y | strict |
| 15 | `DELETE` | `/context/ontologies/{ontology_id}` | `delete_ontology` | Y | Y | strict |
| 16 | `POST` | `/context/ontologies/{ontology_id}/knowledge` | `add_knowledge` | Y | Y | blocked-xfail (`D260-007`) |
| 17 | `POST` | `/context/ontologies/{ontology_id}/entity` | `add_entity` | Y | Y | blocked-xfail (`D260-007`) |
| 18 | `POST` | `/context/ontologies/{ontology_id}/search` | `search_knowledge` | Y | Y | blocked-xfail (`D260-007`) |
| 19 | `POST` | `/context/ontologies/{ontology_id}/memory` | `get_memory` | Y | Y | blocked-xfail (`D260-007`) |
| 20 | `GET` | `/context/ontologies/{ontology_id}/episodes/{group_id}` | `get_episodes` | Y | Y | blocked-xfail (`D260-007`) |
| 21 | `DELETE` | `/context/ontologies/{ontology_id}/groups/{group_id}` | `delete_group` | Y | Y | blocked-xfail (`D260-007`) |
| 22 | `GET` | `/context/ontologies/{ontology_id}/health` | `ontology_health` | Y | Y | strict |
| 23 | `GET` | `/context/collections/` | `list_collections` | Y | Y | blocked-xfail (`D260-005`) |
| 24 | `POST` | `/context/collections/` | `create_collection` | Y | Y | blocked-xfail (`D260-005`) |
| 25 | `GET` | `/context/collections/{collection_name}` | `get_collection` | Y | Y | blocked-xfail (`D260-005`) |
| 26 | `DELETE` | `/context/collections/{collection_name}` | `delete_collection` | Y | Y | blocked-xfail (`D260-005`) |
| 27 | `POST` | `/context/pipelines/` | `create_pipeline_job` | Y | Y | strict |
| 28 | `GET` | `/context/pipelines/` | `list_pipeline_jobs` | Y | Y | strict |
| 29 | `GET` | `/context/pipelines/supported-types` | `get_supported_file_types` | Y | Y | strict |
| 30 | `GET` | `/context/pipelines/{job_id}` | `get_pipeline_job` | Y | Y | blocked-xfail (`D150-008`) |
| 31 | `DELETE` | `/context/pipelines/{job_id}` | `cancel_pipeline_job` | Y | Y | blocked-xfail (`D150-008`) |
| 32 | `POST` | `/context/search` | `search` | Y | Y | blocked-xfail (`D260-006`) |
| 33 | `POST` | `/context/retrieve` | `retrieve` | Y | Y | blocked-xfail (`D260-006`) |
| 34 | `POST` | `/context/agentic/search` | `agentic_search` | Y | Y | blocked-xfail (`D260-006`) |
| 35 | `POST` | `/context/upload/` | `upload_file` | Y | Y | strict |

## Coverage Summary

- Total endpoints: **35**
- Unit coverage by method call presence: **35/35**
- Live coverage by method call presence: **35/35**
- Main gap is now explicit blocker coverage (`xfail`) for known broken backend flows, not broad tolerant assertions.

## Next Actions

- Keep blocked tests pinned to specific defect IDs with strict `xfail` until backend fixes land.
- Promote blocked tests to strict pass checks as each defect closes.
