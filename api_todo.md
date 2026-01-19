# API Integration Coverage Chart

Source: FastAPI routers in /Users/matt/code/kamiwaza (reachable from kamiwaza.main).
Coverage: tests in tests/integration (direct client calls + SDK service method mapping).
Note: CLI/authenticator flows are mapped manually; OpenAI client calls are listed separately.

Total endpoints: 266
Covered by integration tests: 103
Missing integration coverage: 163


## (TS0) ACTIVITY

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS0.001 | [ ] | GET | /activity/activities/ |  |

## (TS1) APPS

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS1.001 | [ ] | GET | /apps/app_templates |  |
| TS1.002 | [ ] | POST | /apps/app_templates |  |
| TS1.003 | [ ] | DELETE | /apps/app_templates/{template_id} |  |
| TS1.004 | [ ] | GET | /apps/app_templates/{template_id} |  |
| TS1.005 | [ ] | PUT | /apps/app_templates/{template_id} |  |
| TS1.006 | [ ] | GET | /apps/config/ephemeral_forced |  |
| TS1.007 | [ ] | POST | /apps/deploy_app |  |
| TS1.008 | [ ] | DELETE | /apps/deployment/{deployment_id} |  |
| TS1.009 | [ ] | GET | /apps/deployment/{deployment_id} |  |
| TS1.010 | [ ] | DELETE | /apps/deployment/{deployment_id}/purge |  |
| TS1.011 | [ ] | GET | /apps/deployment/{deployment_id}/status |  |
| TS1.012 | [ ] | GET | /apps/deployments |  |
| TS1.013 | [ ] | POST | /apps/garden/import |  |
| TS1.014 | [ ] | GET | /apps/garden/status |  |
| TS1.015 | [ ] | POST | /apps/images/pull/{template_id} |  |
| TS1.016 | [ ] | GET | /apps/images/status/{template_id} |  |
| TS1.017 | [ ] | GET | /apps/instance/{instance_id} |  |
| TS1.018 | [ ] | GET | /apps/instances |  |
| TS1.019 | [ ] | GET | /apps/kamiwaza_garden |  |
| TS1.020 | [ ] | GET | /apps/remote/apps |  |
| TS1.021 | [ ] | GET | /apps/remote/status |  |
| TS1.022 | [ ] | POST | /apps/remote/sync |  |
| TS1.023 | [ ] | POST | /apps/sessions/end |  |
| TS1.024 | [ ] | POST | /apps/sessions/heartbeat |  |

## (TS2) AUTH

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS2.001 | [x] | GET | /auth/ | test_auth_endpoints_live.py::test_auth_metadata_health_jwks (direct) |
| TS2.002 | [ ] | GET | /auth/audit/decisions/export |  |
| TS2.003 | [ ] | POST | /auth/cac/login |  |
| TS2.004 | [ ] | GET | /auth/callback |  |
| TS2.005 | [ ] | POST | /auth/check |  |
| TS2.006 | [x] | GET | /auth/forward/validate | test_auth_endpoints_live.py::test_auth_validate_forwardauth_endpoints (direct) |
| TS2.007 | [x] | GET | /auth/health | test_auth_endpoints_live.py::test_auth_metadata_health_jwks (direct) |
| TS2.008 | [ ] | GET | /auth/idp/providers |  |
| TS2.009 | [ ] | GET | /auth/idp/public/providers |  |
| TS2.010 | [ ] | POST | /auth/idp/register |  |
| TS2.011 | [ ] | DELETE | /auth/idp/{alias} |  |
| TS2.012 | [ ] | PATCH | /auth/idp/{alias} |  |
| TS2.013 | [ ] | PUT | /auth/idp/{alias} |  |
| TS2.014 | [x] | GET | /auth/jwks | test_auth_endpoints_live.py::test_auth_metadata_health_jwks (direct) |
| TS2.015 | [ ] | GET | /auth/login |  |
| TS2.016 | [x] | POST | /auth/logout | test_auth_endpoints_live.py::test_auth_logout (auth.logout) |
| TS2.017 | [ ] | GET | /auth/logout/front-channel |  |
| TS2.018 | [ ] | GET | /auth/mint |  |
| TS2.019 | [x] | GET | /auth/pats | test_auth_endpoints_live.py::test_auth_pat_list (auth.list_pats) |
| TS2.020 | [x] | POST | /auth/pats | test_auth_live.py::test_pat_lifecycle_supports_api_key_auth (auth.create_pat); test_cli_live.py::test_cli_login_and_pat_flow (CLI pat create) |
| TS2.021 | [x] | DELETE | /auth/pats/{jti} | test_auth_live.py::test_pat_lifecycle_supports_api_key_auth (auth.revoke_pat) |
| TS2.022 | [x] | POST | /auth/refresh | test_auth_endpoints_live.py::test_auth_refresh_flow (auth.refresh_access_token) |
| TS2.023 | [ ] | POST | /auth/saml/acs |  |
| TS2.024 | [ ] | GET | /auth/saml/login |  |
| TS2.025 | [ ] | GET | /auth/saml/metadata |  |
| TS2.026 | [ ] | GET | /auth/saml/sls |  |
| TS2.027 | [ ] | POST | /auth/saml/sls |  |
| TS2.028 | [ ] | POST | /auth/sessions/purge |  |
| TS2.029 | [ ] | DELETE | /auth/sessions/{session_id} |  |
| TS2.030 | [x] | POST | /auth/token | test_auth_live.py::test_password_authentication_allows_whoami (UserPasswordAuthenticator); test_cli_live.py::test_cli_login_and_pat_flow (CLI login) |
| TS2.031 | [ ] | DELETE | /auth/tuples |  |
| TS2.032 | [ ] | POST | /auth/tuples |  |
| TS2.033 | [ ] | GET | /auth/tuples/ |  |
| TS2.034 | [ ] | POST | /auth/tuples/diff |  |
| TS2.035 | [ ] | GET | /auth/tuples/export |  |
| TS2.036 | [ ] | DELETE | /auth/tuples/object |  |
| TS2.037 | [ ] | POST | /auth/tuples/revoke |  |
| TS2.038 | [ ] | GET | /auth/users/ |  |
| TS2.039 | [ ] | POST | /auth/users/local |  |
| TS2.040 | [x] | GET | /auth/users/me | test_00_current_user.py::test_current_user_is_resolvable (auth.get_current_user); test_auth_live.py::test_password_authentication_allows_whoami (auth.get_current_user); test_auth_live.py::test_pat_lifecycle_supports_api_key_auth (auth.get_current_user); test_catalog_live.py::_resolve_owner (direct) |
| TS2.041 | [ ] | POST | /auth/users/me/password |  |
| TS2.042 | [ ] | DELETE | /auth/users/{user_id} |  |
| TS2.043 | [ ] | GET | /auth/users/{user_id} |  |
| TS2.044 | [ ] | PUT | /auth/users/{user_id} |  |
| TS2.045 | [ ] | POST | /auth/users/{user_id}/password |  |
| TS2.046 | [x] | GET | /auth/validate | test_auth_endpoints_live.py::test_auth_validate_forwardauth_endpoints (direct) |
| TS2.047 | [x] | POST | /auth/validate | test_auth_endpoints_live.py::test_auth_validate_forwardauth_endpoints (direct) |

## (TS3) CATALOG

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS3.001 | [x] | GET | /catalog/ | test_catalog_endpoints.py::test_catalog_metadata_and_health (direct) |
| TS3.002 | [x] | GET | /catalog/containers/ | test_catalog_endpoints.py::test_catalog_container_endpoints (catalog.containers.list) |
| TS3.003 | [x] | POST | /catalog/containers/ | test_catalog_endpoints.py::test_catalog_container_endpoints (catalog.containers.create) |
| TS3.004 | [x] | DELETE | /catalog/containers/by-urn | test_catalog_endpoints.py::test_catalog_container_endpoints (direct cleanup) |
| TS3.005 | [x] | GET | /catalog/containers/by-urn | test_catalog_endpoints.py::test_catalog_container_endpoints (direct) |
| TS3.006 | [x] | PATCH | /catalog/containers/by-urn | test_catalog_endpoints.py::test_catalog_container_endpoints (direct) |
| TS3.007 | [x] | DELETE | /catalog/containers/by-urn/datasets | test_catalog_endpoints.py::test_catalog_container_endpoints (direct) |
| TS3.008 | [x] | POST | /catalog/containers/by-urn/datasets | test_catalog_endpoints.py::test_catalog_container_endpoints (direct) |
| TS3.009 | [x] | DELETE | /catalog/containers/v2/{container_urn:container_urn} | test_catalog_endpoints.py::test_catalog_container_endpoints (direct) |
| TS3.010 | [x] | GET | /catalog/containers/v2/{container_urn:container_urn} | test_catalog_endpoints.py::test_catalog_container_endpoints (direct) |
| TS3.011 | [x] | PATCH | /catalog/containers/v2/{container_urn:container_urn} | test_catalog_endpoints.py::test_catalog_container_endpoints (direct) |
| TS3.012 | [x] | POST | /catalog/containers/v2/{container_urn:container_urn}/datasets | test_catalog_endpoints.py::test_catalog_container_endpoints (direct) |
| TS3.013 | [x] | DELETE | /catalog/containers/v2/{container_urn:container_urn}/datasets/{dataset_urn:dataset_urn} | test_catalog_endpoints.py::test_catalog_container_endpoints (direct) |
| TS3.014 | [x] | GET | /catalog/containers/{container_urn:path} | test_catalog_endpoints.py::test_catalog_container_endpoints (direct) |
| TS3.015 | [x] | PATCH | /catalog/containers/{container_urn:path} | test_catalog_endpoints.py::test_catalog_container_endpoints (direct) |
| TS3.016 | [x] | POST | /catalog/containers/{container_urn:path}/datasets | test_catalog_endpoints.py::test_catalog_container_endpoints (direct) |
| TS3.017 | [x] | DELETE | /catalog/containers/{container_urn:path}/datasets/{dataset_urn:path} | test_catalog_endpoints.py::test_catalog_container_endpoints (direct) |
| TS3.018 | [x] | DELETE | /catalog/containers/{container_urn} | test_catalog_endpoints.py::test_catalog_container_endpoints (direct) |
| TS3.019 | [x] | GET | /catalog/datasets/ | test_catalog_endpoints.py::test_catalog_dataset_schema_endpoints (catalog.datasets.list) |
| TS3.020 | [x] | POST | /catalog/datasets/ | test_catalog_endpoints.py::test_catalog_dataset_schema_endpoints (catalog.datasets.create); test_catalog_live.py::test_catalog_dataset_and_secret_lifecycle (catalog.create_dataset) |
| TS3.021 | [x] | DELETE | /catalog/datasets/by-urn | test_catalog_ingest_retrieval.py::test_s3_ingest_and_retrieve_grpc (direct); test_catalog_ingest_retrieval.py::test_s3_ingest_and_retrieve_inline (direct); test_catalog_multi_source.py::_cleanup_datasets (direct) |
| TS3.022 | [x] | GET | /catalog/datasets/by-urn | test_catalog_ingest_retrieval.py::_ingest_sample_dataset (direct); test_catalog_live.py::test_catalog_dataset_and_secret_lifecycle (catalog.create_dataset); test_catalog_live.py::test_catalog_dataset_and_secret_lifecycle (catalog.get_dataset); test_catalog_multi_source.py::_fetch_dataset (direct) |
| TS3.023 | [x] | PATCH | /catalog/datasets/by-urn | test_catalog_multi_source.py::_ensure_retrieval_metadata (direct); test_catalog_multi_source.py::test_catalog_file_ingestion_metadata (direct) |
| TS3.024 | [x] | GET | /catalog/datasets/by-urn/schema | test_catalog_endpoints.py::test_catalog_dataset_schema_endpoints (direct) |
| TS3.025 | [x] | PUT | /catalog/datasets/by-urn/schema | test_catalog_endpoints.py::test_catalog_dataset_schema_endpoints (direct) |
| TS3.026 | [x] | DELETE | /catalog/datasets/v2/{dataset_urn:dataset_urn} | test_catalog_endpoints.py::test_catalog_dataset_delete_variants (direct) |
| TS3.027 | [x] | GET | /catalog/datasets/v2/{dataset_urn:dataset_urn} | test_catalog_endpoints.py::test_catalog_dataset_variant_endpoints (direct) |
| TS3.028 | [x] | PATCH | /catalog/datasets/v2/{dataset_urn:dataset_urn} | test_catalog_endpoints.py::test_catalog_dataset_variant_endpoints (direct) |
| TS3.029 | [x] | GET | /catalog/datasets/v2/{dataset_urn:dataset_urn}/schema | test_catalog_endpoints.py::test_catalog_dataset_schema_endpoints (direct) |
| TS3.030 | [x] | PUT | /catalog/datasets/v2/{dataset_urn:dataset_urn}/schema | test_catalog_endpoints.py::test_catalog_dataset_schema_endpoints (direct) |
| TS3.031 | [x] | DELETE | /catalog/datasets/{dataset_urn:path} | test_catalog_endpoints.py::test_catalog_dataset_delete_variants (direct) |
| TS3.032 | [x] | GET | /catalog/datasets/{dataset_urn:path} | test_catalog_endpoints.py::test_catalog_dataset_variant_endpoints (direct) |
| TS3.033 | [x] | PATCH | /catalog/datasets/{dataset_urn:path} | test_catalog_endpoints.py::test_catalog_dataset_variant_endpoints (direct) |
| TS3.034 | [x] | GET | /catalog/health | test_catalog_endpoints.py::test_catalog_metadata_and_health (direct) |
| TS3.035 | [x] | GET | /catalog/secrets/ | test_catalog_endpoints.py::test_catalog_secret_list (catalog.secrets.list); test_catalog_live.py::test_catalog_dataset_and_secret_lifecycle (catalog.list_secrets) |
| TS3.036 | [x] | POST | /catalog/secrets/ | test_catalog_endpoints.py::test_catalog_secret_endpoints (catalog.secrets.create) |
| TS3.037 | [x] | DELETE | /catalog/secrets/by-urn | test_catalog_endpoints.py::test_catalog_secret_endpoints (direct) |
| TS3.038 | [x] | GET | /catalog/secrets/by-urn | test_catalog_endpoints.py::test_catalog_secret_endpoints (direct) |
| TS3.039 | [x] | DELETE | /catalog/secrets/v2/{secret_urn:secret_urn} | test_catalog_endpoints.py::test_catalog_secret_endpoints (direct) |
| TS3.040 | [x] | GET | /catalog/secrets/v2/{secret_urn:secret_urn} | test_catalog_endpoints.py::test_catalog_secret_endpoints (direct) |
| TS3.041 | [x] | DELETE | /catalog/secrets/{secret_urn} | test_catalog_endpoints.py::test_catalog_secret_endpoints (direct) |
| TS3.042 | [x] | GET | /catalog/secrets/{secret_urn} | test_catalog_endpoints.py::test_catalog_secret_endpoints (direct) |

## (TS4) CLUSTER

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS4.001 | [x] | POST | /cluster/attach_pairing | test_cluster_live.py::TestFederationOperationsNotAvailable::test_attach_pairing - SKIPPED: Two-node pairing moved to .env configuration |
| TS4.002 | [x] | POST | /cluster/cluster | test_cluster_live.py::TestClusterLifecycle::test_cluster_lifecycle (cluster.create_cluster) |
| TS4.003 | [x] | GET | /cluster/cluster/{cluster_id} | test_cluster_live.py::TestClusterLifecycle::test_cluster_lifecycle; test_cluster_live.py::TestClusterLifecycle::test_get_nonexistent_cluster (cluster.get_cluster) |
| TS4.004 | [x] | GET | /cluster/cluster_capabilities | test_cluster_live.py::TestClusterCapabilities::test_get_cluster_capabilities (direct) |
| TS4.005 | [x] | POST | /cluster/cluster_federation_reciprocation | test_cluster_live.py::TestFederationOperationsNotAvailable::test_federation_reciprocation - SKIPPED: Requires remote cluster |
| TS4.006 | [x] | GET | /cluster/clusters | test_cluster_live.py::TestClusterReadOperations::test_list_clusters (cluster.list_clusters) |
| TS4.007 | [x] | POST | /cluster/detach_pairing | test_cluster_live.py::TestFederationOperationsNotAvailable::test_detach_pairing - SKIPPED: Two-node pairing moved to .env configuration |
| TS4.008 | [x] | POST | /cluster/disconnect_federation | test_cluster_live.py::TestFederationOperationsNotAvailable::test_disconnect_federation_handler - SKIPPED: Requires remote cluster |
| TS4.009 | [ ] | GET | /cluster/dummy_node_list_node | Internal endpoint (include_in_schema=False) |
| TS4.010 | [x] | GET | /cluster/federations | test_cluster_live.py::TestFederationListOperations::test_list_federations (direct) |
| TS4.011 | [x] | POST | /cluster/federations | test_cluster_live.py::TestFederationOperationsNotAvailable::test_create_federation - SKIPPED: Requires second cluster |
| TS4.012 | [x] | DELETE | /cluster/federations/{federation_id} | test_cluster_live.py::TestFederationOperationsNotAvailable::test_delete_federation - SKIPPED: Requires existing federation |
| TS4.013 | [x] | GET | /cluster/federations/{federation_id} | test_cluster_live.py::TestFederationOperationsNotAvailable::test_get_federation - SKIPPED: Requires existing federation |
| TS4.014 | [x] | PUT | /cluster/federations/{federation_id} | test_cluster_live.py::TestFederationOperationsNotAvailable::test_update_federation - SKIPPED: Requires existing federation |
| TS4.015 | [x] | POST | /cluster/federations/{federation_id}/disconnect | test_cluster_live.py::TestFederationOperationsNotAvailable::test_disconnect_federation - SKIPPED: Requires existing federation |
| TS4.016 | [x] | POST | /cluster/federations/{federation_id}/pair | test_cluster_live.py::TestFederationOperationsNotAvailable::test_pair_federation - SKIPPED: Requires existing federation |
| TS4.017 | [x] | POST | /cluster/federations/{federation_id}/ping | test_cluster_live.py::TestFederationOperationsNotAvailable::test_ping_federation - SKIPPED: Requires existing federation |
| TS4.018 | [x] | GET | /cluster/get_hostname | test_cluster_live.py::TestClusterReadOperations::test_get_hostname (cluster.get_hostname) |
| TS4.019 | [x] | GET | /cluster/get_running_nodes | test_cluster_live.py::TestClusterReadOperations::test_get_running_nodes (cluster.get_running_nodes) |
| TS4.020 | [x] | GET | /cluster/hardware | test_cluster_live.py::TestClusterReadOperations::test_list_hardware (cluster.list_hardware) |
| TS4.021 | [x] | POST | /cluster/hardware | test_cluster_live.py::TestHardwareLifecycle::test_create_and_get_hardware (cluster.create_hardware) |
| TS4.022 | [x] | GET | /cluster/hardware/{hardware_id} | test_cluster_live.py::TestHardwareLifecycle::test_create_and_get_hardware; test_cluster_live.py::TestHardwareLifecycle::test_get_nonexistent_hardware (cluster.get_hardware) |
| TS4.023 | [x] | POST | /cluster/location | test_cluster_live.py::TestClusterLocationLifecycle::test_location_lifecycle (cluster.create_location) |
| TS4.024 | [x] | GET | /cluster/location/{location_id} | test_cluster_live.py::TestClusterLocationLifecycle::test_location_lifecycle (cluster.get_location) |
| TS4.025 | [x] | PUT | /cluster/location/{location_id} | test_cluster_live.py::TestClusterLocationLifecycle::test_location_lifecycle (cluster.update_location) |
| TS4.026 | [x] | GET | /cluster/locations | test_cluster_live.py::TestClusterReadOperations::test_list_locations (cluster.list_locations) |
| TS4.027 | [x] | GET | /cluster/node/{node_id} | test_cluster_live.py::TestClusterNodeDetails::test_get_node_by_id (cluster.get_node_by_id) |
| TS4.028 | [x] | GET | /cluster/nodes | test_cluster_live.py::TestClusterReadOperations::test_list_nodes (cluster.list_nodes) |
| TS4.029 | [x] | POST | /cluster/pair_federation | test_cluster_live.py::TestFederationOperationsNotAvailable::test_pair_federation_handler - SKIPPED: Requires remote cluster |
| TS4.030 | [x] | POST | /cluster/refresh_hardware | test_cluster_live.py::TestHardwareRefresh::test_refresh_hardware (direct) |
| TS4.031 | [x] | GET | /cluster/runtime_config | test_cluster_live.py::TestClusterReadOperations::test_get_runtime_config (cluster.get_runtime_config) |

## (TS5) CONFIG

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS5.001 | [ ] | GET | /config/routing |  |
| TS5.002 | [ ] | PATCH | /config/routing |  |

## (TS6) EMBEDDING

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS6.001 | [x] | POST | /embedding/batch | test_embedding_live.py::TestEmbeddingGeneration::test_batch_embeddings (embedding.embedder.batch_embed) |
| TS6.002 | [x] | POST | /embedding/chunk | test_embedding_live.py::TestEmbeddingChunking::test_chunk_text_simple; test_embedding_live.py::TestEmbeddingChunking::test_chunk_text_overlap_handling (embedding.embedder.chunk_text) |
| TS6.003 | [x] | POST | /embedding/generate | test_embedding_live.py::TestEmbeddingGeneration::test_generate_embeddings_basic; test_embedding_live.py::TestEmbeddingGeneration::test_generate_embeddings_batch (embedding.embedder.embed) |
| TS6.004 | [ ] | GET | /embedding/generate/{text} |  |
| TS6.005 | [x] | GET | /embedding/health | test_embedding_live.py::TestEmbeddingHealth::test_embedding_health (embedding.health) |
| TS6.006 | [x] | GET | /embedding/providers | test_embedding_live.py::TestEmbeddingProviders::test_list_providers (embedding.list_providers) |

## (TS7) GUIDE

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS7.001 | [x] | GET | /guide/ | test_guide_live.py::TestGuideReadOperations::test_list_guides; test_guide_live.py::TestGuideReadOperations::test_list_guides_with_normalized_use_case (models.list_guides) |
| TS7.002 | [x] | POST | /guide/import | test_guide_live.py::TestGuideImportOperations::test_import_guides (models.import_guides) |
| TS7.003 | [x] | POST | /guide/refresh | test_guide_live.py::TestGuideRefreshOperations::test_refresh_guides (models.refresh_guides) |

## (TS8) INGESTION

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS8.001 | [ ] | GET | /ingestion/api/dde/connectors/ |  |
| TS8.002 | [ ] | POST | /ingestion/api/dde/connectors/ |  |
| TS8.003 | [ ] | DELETE | /ingestion/api/dde/connectors/{connector_id} |  |
| TS8.004 | [ ] | GET | /ingestion/api/dde/connectors/{connector_id} |  |
| TS8.005 | [ ] | PUT | /ingestion/api/dde/connectors/{connector_id} |  |
| TS8.006 | [ ] | POST | /ingestion/api/dde/connectors/{connector_id}/trigger_ingest |  |
| TS8.007 | [ ] | GET | /ingestion/api/dde/documents/ |  |
| TS8.008 | [ ] | POST | /ingestion/api/dde/documents/ |  |
| TS8.009 | [ ] | GET | /ingestion/api/dde/documents/{document_id} |  |
| TS8.010 | [ ] | GET | /ingestion/health |  |
| TS8.011 | [ ] | POST | /ingestion/ingest/emit |  |
| TS8.012 | [ ] | POST | /ingestion/ingest/jobs |  |
| TS8.013 | [x] | POST | /ingestion/ingest/run | test_catalog_ingest_retrieval.py::_ingest_sample_dataset (ingestion.run_active); test_catalog_multi_source.py::_ingest_object_dataset (ingestion.run_active); test_catalog_multi_source.py::test_catalog_file_ingestion_metadata (ingestion.run_active); test_catalog_multi_source.py::test_catalog_kafka_ingestion_metadata (ingestion.run_active); test_catalog_multi_source.py::test_catalog_object_ingestion_inline_retrieval (ingestion.run_active); test_catalog_multi_source.py::test_catalog_parquet_ingestion_inline_retrieval (ingestion.run_active); test_catalog_multi_source.py::test_catalog_postgres_ingestion_metadata (ingestion.run_active); test_catalog_multi_source.py::test_catalog_slack_ingestion_metadata (ingestion.run_slack_ingest) |
| TS8.014 | [ ] | GET | /ingestion/ingest/status/{job_id} |  |

## (TS9) LOGGER

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS9.001 | [ ] | DELETE | /logger/logger/cleanup |  |
| TS9.002 | [ ] | GET | /logger/logger/deployment/{deployment_id}/logs |  |
| TS9.003 | [ ] | GET | /logger/logger/deployment/{deployment_id}/logs/patterns |  |
| TS9.004 | [ ] | GET | /logger/logger/deployments/all |  |
| TS9.005 | [ ] | GET | /logger/logger/deployments/orphaned |  |
| TS9.006 | [ ] | GET | /logger/logger/deployments/type/{deployment_type} |  |
| TS9.007 | [ ] | DELETE | /logger/logger/deployments/{deployment_type}/{deployment_id} |  |
| TS9.008 | [ ] | GET | /logger/logger/deployments/{deployment_type}/{deployment_id} |  |
| TS9.009 | [ ] | GET | /logger/logger/deployments/{deployment_type}/{deployment_id}/patterns |  |
| TS9.010 | [ ] | GET | /logger/logger/engine/{engine_type} |  |
| TS9.011 | [ ] | GET | /logger/logger/stats |  |

## (TS10) MODEL_CONFIGS

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS10.001 | [x] | GET | /model_configs/ | test_serving_workflow.py::test_deploy_qwen_and_infer_with_strip_thinking (models.get_model_configs); test_model_configs_live.py::TestModelConfigListOperations::test_get_model_configs (models.get_model_configs) |
| TS10.002 | [x] | POST | /model_configs/ | test_serving_workflow.py::test_deploy_qwen_and_infer_with_strip_thinking (models.create_model_config); test_model_configs_live.py::TestModelConfigLifecycle::test_create_and_delete_model_config (models.create_model_config) |
| TS10.003 | [x] | DELETE | /model_configs/{model_config_id} | test_serving_workflow.py::test_deploy_qwen_and_infer_with_strip_thinking (models.delete_model_config); test_model_configs_live.py::TestModelConfigLifecycle::test_create_and_delete_model_config (models.delete_model_config) |
| TS10.004 | [x] | GET | /model_configs/{model_config_id} | test_model_configs_live.py::TestModelConfigReadOperations::test_get_model_config_by_id (models.get_model_config) |
| TS10.005 | [x] | PUT | /model_configs/{model_config_id} | test_model_configs_live.py::TestModelConfigLifecycle::test_update_model_config (models.update_model_config) |

## (TS11) MODEL_FILES

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS11.001 | [x] | GET | /model_files/ | test_model_files_live.py::TestModelFileReadOperations::test_list_model_files (models.list_model_files) |
| TS11.002 | [x] | POST | /model_files/ | test_model_files_live.py::TestModelFileCreateAndDelete::test_create_model_file (models.create_model_file) - SKIPPED: Server defect, returns 500 (see 00-server-defects.md) |
| TS11.003 | [x] | GET | /model_files/download_status/ | conftest.py::_ensure (models.wait_for_download); test_model_files_live.py::TestModelFileDownloadStatus::test_get_download_status (models.get_model_files_download_status) |
| TS11.004 | [x] | DELETE | /model_files/downloads/cancel_all | test_model_files_live.py::TestModelFileDownloadOperations::test_cancel_all_downloads (direct) - SKIPPED: May affect other tests |
| TS11.005 | [x] | POST | /model_files/search/ | test_model_files_live.py::TestModelFileSearch::test_search_hub_model_files_with_dict; test_model_files_live.py::TestModelFileSearch::test_search_hub_model_files_with_schema (models.search_hub_model_files) - SKIPPED: Server returns 500 (see 00-server-defects.md) |
| TS11.006 | [x] | DELETE | /model_files/{model_file_id} | test_model_files_live.py::TestModelFileCreateAndDelete::test_delete_nonexistent_model_file; test_model_files_live.py::TestModelFileCreateAndDelete::test_delete_existing_model_file (models.delete_model_file) |
| TS11.007 | [x] | GET | /model_files/{model_file_id} | test_model_files_live.py::TestModelFileReadOperations::test_get_model_file_by_id (models.get_model_file) |
| TS11.008 | [x] | DELETE | /model_files/{model_file_id}/download | test_model_files_live.py::TestModelFileDownloadOperations::test_cancel_specific_download (direct) - SKIPPED: May affect other tests |
| TS11.009 | [x] | GET | /model_files/{model_file_id}/memory_usage | test_model_files_live.py::TestModelFileMemoryUsage::test_get_model_file_memory_usage (models.get_model_file_memory_usage) |

## (TS12) MODELS

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS12.001 | [x] | GET | /models/ | conftest.py::_ensure (models.get_model_by_repo_id); test_models_extended_live.py::TestModelListOperations::test_list_models; test_models_extended_live.py::TestModelListOperations::test_list_models_with_files (models.list_models) |
| TS12.002 | [ ] | POST | /models/ |  |
| TS12.003 | [x] | POST | /models/cleanup_stale_deployments | test_models_extended_live.py::TestModelCleanupOperations::test_cleanup_stale_deployments (direct) |
| TS12.004 | [ ] | POST | /models/deploy_after_download/{model_key} |  |
| TS12.005 | [x] | POST | /models/download/ | conftest.py::_ensure (models.initiate_model_download); test_models_live.py::test_live_model_metadata_and_download (direct); test_serving_workflow.py::_ensure_model_cached (direct) |
| TS12.006 | [ ] | POST | /models/download_and_deploy |  |
| TS12.007 | [x] | GET | /models/pending_deployments | test_models_extended_live.py::TestModelDeploymentInfo::test_get_pending_deployments (direct) |
| TS12.008 | [x] | POST | /models/search/ | test_models_extended_live.py::TestModelSearchOperations::test_search_models_by_repo_id; test_models_extended_live.py::TestModelSearchOperations::test_search_models_exact_match; test_models_extended_live.py::TestModelSearchOperations::test_get_model_by_repo_id (models.search_models) |
| TS12.009 | [ ] | DELETE | /models/{model_id} |  |
| TS12.010 | [x] | GET | /models/{model_id} | test_models_live.py::test_live_model_metadata_and_download (models.get_model); test_models_extended_live.py::TestModelDetailOperations::test_get_model (models.get_model) |
| TS12.011 | [x] | GET | /models/{model_id}/configs | test_models_extended_live.py::TestModelDetailOperations::test_get_model_configs_via_model (models.get_model_configs_for_model) |
| TS12.012 | [x] | GET | /models/{model_id}/deployment_info | test_models_extended_live.py::TestModelDeploymentInfo::test_get_model_deployment_info (direct) |
| TS12.013 | [x] | GET | /models/{model_id}/memory_usage | test_models_extended_live.py::TestModelDetailOperations::test_get_model_memory_usage (models.get_model_memory_usage) |

## (TS13) NEWS

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS13.001 | [x] | GET | /news/latest | test_news_live.py::TestNewsEndpoints::test_get_latest_news (direct) |
| TS13.002 | [x] | GET | /news/quadrants | test_news_live.py::TestNewsEndpoints::test_get_news_quadrants (direct) |

## (TS14) NODE

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS14.001 | [x] | GET | /node/node_id | test_node_live.py::TestNodeEndpoints::test_get_node_id (direct) |
| TS14.002 | [x] | GET | /node/node_status | test_node_live.py::TestNodeEndpoints::test_get_node_status (direct) |

## (TS15) PING

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS15.001 | [x] | GET | /ping | test_ping_live.py::TestPingEndpoint::test_ping (direct) |

## (TS16) PROMPTS

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS16.001 | [x] | POST | /prompts/elements/ | test_prompts_live.py::TestPromptElementOperations::test_create_and_get_element (prompts.create_element) |
| TS16.002 | [x] | GET | /prompts/elements/{element_id} | test_prompts_live.py::TestPromptElementOperations::test_create_and_get_element (prompts.get_element); test_prompts_live.py::TestPromptElementOperations::test_get_nonexistent_element (prompts.get_element) |
| TS16.003 | [x] | POST | /prompts/roles/ | test_prompts_live.py::TestPromptRoleOperations::test_create_and_get_role (prompts.create_role) |
| TS16.004 | [x] | GET | /prompts/roles/{role_id} | test_prompts_live.py::TestPromptRoleOperations::test_create_and_get_role (prompts.get_role); test_prompts_live.py::TestPromptRoleOperations::test_get_nonexistent_role (prompts.get_role) |
| TS16.005 | [x] | POST | /prompts/systems/ | test_prompts_live.py::TestPromptSystemOperations::test_create_and_get_system (prompts.create_system) |
| TS16.006 | [x] | GET | /prompts/systems/{system_id} | test_prompts_live.py::TestPromptSystemOperations::test_create_and_get_system (prompts.get_system); test_prompts_live.py::TestPromptSystemOperations::test_get_nonexistent_system (prompts.get_system) |
| TS16.007 | [x] | POST | /prompts/templates/ | test_prompts_live.py::TestPromptTemplateOperations::test_create_and_get_template (prompts.create_template) |
| TS16.008 | [x] | GET | /prompts/templates/{template_id} | test_prompts_live.py::TestPromptTemplateOperations::test_create_and_get_template (prompts.get_template); test_prompts_live.py::TestPromptTemplateOperations::test_get_nonexistent_template (prompts.get_template) |

## (TS17) RETRIEVAL

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS17.001 | [x] | POST | /retrieval/jobs | test_catalog_ingest_retrieval.py::test_s3_ingest_and_retrieve_grpc (direct); test_catalog_ingest_retrieval.py::test_s3_ingest_and_retrieve_inline (direct); test_catalog_multi_source.py::_run_inline_retrieval (direct); test_catalog_multi_source.py::_run_sse_retrieval (direct); test_catalog_multi_source.py::test_catalog_file_ingestion_metadata (direct); test_catalog_multi_source.py::test_catalog_postgres_ingestion_metadata (direct); test_catalog_multi_source.py::test_catalog_slack_ingestion_metadata (retrieval.slack_messages) |
| TS17.002 | [x] | GET | /retrieval/jobs/{job_id} | test_retrieval_live.py::TestRetrievalJobStatus::test_get_job_via_direct_api (direct); test_retrieval_live.py::TestRetrievalJobStatus::test_get_nonexistent_job_status (retrieval.get_job) |
| TS17.003 | [x] | GET | /retrieval/jobs/{job_id}/stream | test_catalog_multi_source.py::_run_sse_retrieval (direct) |

## (TS18) SECURITY

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS18.001 | [x] | POST | /security/consent/accept | test_security_live.py::TestSecurityConsentAccept::test_accept_consent (direct) |
| TS18.002 | [x] | GET | /security/embed.js | test_security_live.py::TestSecurityEmbedScript::test_get_embed_script (direct) |
| TS18.003 | [x] | GET | /security/public/config | test_security_live.py::TestSecurityPublicConfig::test_get_public_config (direct) |

## (TS19) SERVING

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS19.001 | [x] | POST | /serving/deploy_model | test_cli_live.py::test_cli_login_and_pat_flow (CLI serve deploy); test_serving_workflow.py::test_deploy_qwen_and_infer_with_strip_thinking (serving.deploy_model) |
| TS19.002 | [x] | DELETE | /serving/deployment/{deployment_id} | test_cli_live.py::test_cli_login_and_pat_flow (serving.stop_deployment); test_serving_workflow.py::test_deploy_qwen_and_infer_with_strip_thinking (serving.stop_deployment) |
| TS19.003 | [x] | GET | /serving/deployment/{deployment_id} | test_cli_live.py::test_cli_login_and_pat_flow (CLI --wait); test_serving_workflow.py::test_deploy_qwen_and_infer_with_strip_thinking (serving.wait_for_deployment) |
| TS19.004 | [x] | GET | /serving/deployment/{deployment_id}/logs | test_serving_workflow.py::_sample_logs (serving.stream_deployment_logs) |
| TS19.005 | [x] | GET | /serving/deployment/{deployment_id}/logs/patterns | test_serving_endpoints_live.py::test_serving_deployment_status_and_log_patterns (direct) |
| TS19.006 | [x] | GET | /serving/deployment/{deployment_id}/status | test_serving_endpoints_live.py::test_serving_deployment_status_and_log_patterns (direct) |
| TS19.007 | [x] | GET | /serving/deployments | test_serving_endpoints_live.py::test_serving_deployments_and_instances (serving.list_deployments) |
| TS19.008 | [x] | POST | /serving/estimate_model_vram | test_serving_endpoints_live.py::test_serving_estimate_model_vram (direct) |
| TS19.009 | [x] | GET | /serving/health | test_serving_endpoints_live.py::test_serving_status_and_health (direct) |
| TS19.010 | [x] | GET | /serving/logs/{engine_type} | test_serving_endpoints_live.py::test_serving_engine_logs (direct) |
| TS19.011 | [x] | GET | /serving/model_instance/{instance_id} | test_serving_endpoints_live.py::test_serving_deployments_and_instances (serving.get_model_instance) |
| TS19.012 | [x] | GET | /serving/model_instances | test_serving_endpoints_live.py::test_serving_deployments_and_instances (serving.list_model_instances) |
| TS19.013 | [x] | POST | /serving/start | test_serving_endpoints_live.py::test_serving_start_ray (direct, env-gated) |
| TS19.014 | [x] | GET | /serving/status | test_serving_endpoints_live.py::test_serving_status_and_health (direct) |

## (TS20) TOOL

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS20.001 | [ ] | POST | /tool/deploy |  |
| TS20.002 | [ ] | POST | /tool/deploy-template/{template_name} |  |
| TS20.003 | [ ] | DELETE | /tool/deployment/{deployment_id} |  |
| TS20.004 | [ ] | GET | /tool/deployment/{deployment_id} |  |
| TS20.005 | [ ] | GET | /tool/deployment/{deployment_id}/health |  |
| TS20.006 | [ ] | GET | /tool/deployments |  |
| TS20.007 | [ ] | GET | /tool/discover |  |
| TS20.008 | [ ] | POST | /tool/garden/import |  |
| TS20.009 | [ ] | GET | /tool/garden/status |  |
| TS20.010 | [ ] | GET | /tool/remote/status |  |
| TS20.011 | [ ] | POST | /tool/remote/sync |  |
| TS20.012 | [ ] | GET | /tool/remote/tools |  |
| TS20.013 | [ ] | GET | /tool/templates |  |
| TS20.014 | [ ] | GET | /tool/templates/available |  |
| TS20.015 | [ ] | DELETE | /tool/tool_templates/{template_id} |  |
| TS20.016 | [ ] | PUT | /tool/tool_templates/{template_id} |  |

## (TS21) VECTORDB

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS21.001 | [x] | GET | /vectordb/ | test_vectordb_live.py::TestVectorDBListOperations::test_list_vectordbs; test_vectordb_live.py::TestVectorDBListOperations::test_list_vectordbs_filter_by_engine (vectordb.get_vectordbs) |
| TS21.002 | [x] | POST | /vectordb/ | test_vectordb_live.py::TestVectorDBLifecycle::test_create_get_and_remove_vectordb (vectordb.create_vectordb) |
| TS21.003 | [x] | GET | /vectordb/collections | test_vectordb_live.py::TestVectorDBCollectionOperations::test_list_collections (vectordb.list_collections) |
| TS21.004 | [x] | DELETE | /vectordb/collections/{collection_name} | test_vectordb_live.py::TestVectorDBCollectionOperations::test_drop_nonexistent_collection; test_vectordb_live.py::TestVectorOperations::test_insert_and_search_vectors (vectordb.drop_collection) |
| TS21.005 | [x] | POST | /vectordb/insert_vectors | test_vectordb_live.py::TestVectorOperations::test_insert_and_search_vectors; test_vectordb_live.py::TestVectorDBHelperMethods::test_insert_helper_method (vectordb.insert_vectors) |
| TS21.006 | [x] | POST | /vectordb/search_vectors | test_vectordb_live.py::TestVectorOperations::test_insert_and_search_vectors; test_vectordb_live.py::TestVectorOperations::test_search_with_no_results; test_vectordb_live.py::TestVectorDBHelperMethods::test_search_helper_method (vectordb.search_vectors) |
| TS21.007 | [x] | DELETE | /vectordb/{vectordb_id} | test_vectordb_live.py::TestVectorDBLifecycle::test_create_get_and_remove_vectordb; test_vectordb_live.py::TestVectorDBLifecycle::test_remove_nonexistent_vectordb (vectordb.remove_vectordb) |
| TS21.008 | [x] | GET | /vectordb/{vectordb_id} | test_vectordb_live.py::TestVectorDBLifecycle::test_create_get_and_remove_vectordb; test_vectordb_live.py::TestVectorDBLifecycle::test_get_nonexistent_vectordb (vectordb.get_vectordb) |

## (TS22) WHOAMI

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS22.001 | [x] | GET | /whoami | test_auth_live.py::test_password_authentication_allows_whoami (direct) |

## Unmapped Service Calls

These service calls are used in integration tests but did not resolve to explicit endpoint mappings.

| Service Call | Test |
| --- | --- |
| openai.get_client | test_serving_workflow.py::test_deploy_qwen_and_infer_with_strip_thinking:106 |
| openai.get_client | test_serving_workflow.py::test_deploy_qwen_and_infer_with_strip_thinking:107 |
