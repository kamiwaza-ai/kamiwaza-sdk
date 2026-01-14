# API Integration Coverage Chart

Source: FastAPI routers in /Users/matt/code/kamiwaza (reachable from kamiwaza.main).
Coverage: tests in tests/integration (direct client calls + SDK service method mapping).
Note: CLI/authenticator flows are mapped manually; OpenAI client calls are listed separately.

Total endpoints: 266
Covered by integration tests: 24
Missing integration coverage: 242


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
| TS2.001 | [ ] | GET | /auth/ |  |
| TS2.002 | [ ] | GET | /auth/audit/decisions/export |  |
| TS2.003 | [ ] | POST | /auth/cac/login |  |
| TS2.004 | [ ] | GET | /auth/callback |  |
| TS2.005 | [ ] | POST | /auth/check |  |
| TS2.006 | [ ] | GET | /auth/forward/validate |  |
| TS2.007 | [ ] | GET | /auth/health |  |
| TS2.008 | [ ] | GET | /auth/idp/providers |  |
| TS2.009 | [ ] | GET | /auth/idp/public/providers |  |
| TS2.010 | [ ] | POST | /auth/idp/register |  |
| TS2.011 | [ ] | DELETE | /auth/idp/{alias} |  |
| TS2.012 | [ ] | PATCH | /auth/idp/{alias} |  |
| TS2.013 | [ ] | PUT | /auth/idp/{alias} |  |
| TS2.014 | [ ] | GET | /auth/jwks |  |
| TS2.015 | [ ] | GET | /auth/login |  |
| TS2.016 | [ ] | POST | /auth/logout |  |
| TS2.017 | [ ] | GET | /auth/logout/front-channel |  |
| TS2.018 | [ ] | GET | /auth/mint |  |
| TS2.019 | [ ] | GET | /auth/pats |  |
| TS2.020 | [x] | POST | /auth/pats | test_auth_live.py::test_pat_lifecycle_supports_api_key_auth (auth.create_pat); test_cli_live.py::test_cli_login_and_pat_flow (CLI pat create) |
| TS2.021 | [x] | DELETE | /auth/pats/{jti} | test_auth_live.py::test_pat_lifecycle_supports_api_key_auth (auth.revoke_pat) |
| TS2.022 | [ ] | POST | /auth/refresh |  |
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
| TS2.046 | [ ] | GET | /auth/validate |  |
| TS2.047 | [ ] | POST | /auth/validate |  |

## (TS3) CATALOG

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS3.001 | [ ] | GET | /catalog/ |  |
| TS3.002 | [ ] | GET | /catalog/containers/ |  |
| TS3.003 | [ ] | POST | /catalog/containers/ |  |
| TS3.004 | [ ] | DELETE | /catalog/containers/by-urn |  |
| TS3.005 | [ ] | GET | /catalog/containers/by-urn |  |
| TS3.006 | [ ] | PATCH | /catalog/containers/by-urn |  |
| TS3.007 | [ ] | DELETE | /catalog/containers/by-urn/datasets |  |
| TS3.008 | [ ] | POST | /catalog/containers/by-urn/datasets |  |
| TS3.009 | [ ] | DELETE | /catalog/containers/v2/{container_urn:container_urn} |  |
| TS3.010 | [ ] | GET | /catalog/containers/v2/{container_urn:container_urn} |  |
| TS3.011 | [ ] | PATCH | /catalog/containers/v2/{container_urn:container_urn} |  |
| TS3.012 | [ ] | POST | /catalog/containers/v2/{container_urn:container_urn}/datasets |  |
| TS3.013 | [ ] | DELETE | /catalog/containers/v2/{container_urn:container_urn}/datasets/{dataset_urn:dataset_urn} |  |
| TS3.014 | [ ] | GET | /catalog/containers/{container_urn:path} |  |
| TS3.015 | [ ] | PATCH | /catalog/containers/{container_urn:path} |  |
| TS3.016 | [ ] | POST | /catalog/containers/{container_urn:path}/datasets |  |
| TS3.017 | [ ] | DELETE | /catalog/containers/{container_urn:path}/datasets/{dataset_urn:path} |  |
| TS3.018 | [ ] | DELETE | /catalog/containers/{container_urn} |  |
| TS3.019 | [ ] | GET | /catalog/datasets/ |  |
| TS3.020 | [x] | POST | /catalog/datasets/ | test_catalog_live.py::test_catalog_dataset_and_secret_lifecycle (catalog.create_dataset) |
| TS3.021 | [x] | DELETE | /catalog/datasets/by-urn | test_catalog_ingest_retrieval.py::test_s3_ingest_and_retrieve_grpc (direct); test_catalog_ingest_retrieval.py::test_s3_ingest_and_retrieve_inline (direct); test_catalog_multi_source.py::_cleanup_datasets (direct) |
| TS3.022 | [x] | GET | /catalog/datasets/by-urn | test_catalog_ingest_retrieval.py::_ingest_sample_dataset (direct); test_catalog_live.py::test_catalog_dataset_and_secret_lifecycle (catalog.create_dataset); test_catalog_live.py::test_catalog_dataset_and_secret_lifecycle (catalog.get_dataset); test_catalog_multi_source.py::_fetch_dataset (direct) |
| TS3.023 | [x] | PATCH | /catalog/datasets/by-urn | test_catalog_multi_source.py::_ensure_retrieval_metadata (direct); test_catalog_multi_source.py::test_catalog_file_ingestion_metadata (direct) |
| TS3.024 | [ ] | GET | /catalog/datasets/by-urn/schema |  |
| TS3.025 | [ ] | PUT | /catalog/datasets/by-urn/schema |  |
| TS3.026 | [ ] | DELETE | /catalog/datasets/v2/{dataset_urn:dataset_urn} |  |
| TS3.027 | [ ] | GET | /catalog/datasets/v2/{dataset_urn:dataset_urn} |  |
| TS3.028 | [ ] | PATCH | /catalog/datasets/v2/{dataset_urn:dataset_urn} |  |
| TS3.029 | [ ] | GET | /catalog/datasets/v2/{dataset_urn:dataset_urn}/schema |  |
| TS3.030 | [ ] | PUT | /catalog/datasets/v2/{dataset_urn:dataset_urn}/schema |  |
| TS3.031 | [ ] | DELETE | /catalog/datasets/{dataset_urn:path} |  |
| TS3.032 | [ ] | GET | /catalog/datasets/{dataset_urn:path} |  |
| TS3.033 | [ ] | PATCH | /catalog/datasets/{dataset_urn:path} |  |
| TS3.034 | [ ] | GET | /catalog/health |  |
| TS3.035 | [x] | GET | /catalog/secrets/ | test_catalog_live.py::test_catalog_dataset_and_secret_lifecycle (catalog.list_secrets) |
| TS3.036 | [ ] | POST | /catalog/secrets/ |  |
| TS3.037 | [ ] | DELETE | /catalog/secrets/by-urn |  |
| TS3.038 | [ ] | GET | /catalog/secrets/by-urn |  |
| TS3.039 | [ ] | DELETE | /catalog/secrets/v2/{secret_urn:secret_urn} |  |
| TS3.040 | [ ] | GET | /catalog/secrets/v2/{secret_urn:secret_urn} |  |
| TS3.041 | [ ] | DELETE | /catalog/secrets/{secret_urn} |  |
| TS3.042 | [ ] | GET | /catalog/secrets/{secret_urn} |  |

## (TS4) CLUSTER

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS4.001 | [ ] | POST | /cluster/attach_pairing |  |
| TS4.002 | [ ] | POST | /cluster/cluster |  |
| TS4.003 | [ ] | GET | /cluster/cluster/{cluster_id} |  |
| TS4.004 | [ ] | GET | /cluster/cluster_capabilities |  |
| TS4.005 | [ ] | POST | /cluster/cluster_federation_reciprocation |  |
| TS4.006 | [ ] | GET | /cluster/clusters |  |
| TS4.007 | [ ] | POST | /cluster/detach_pairing |  |
| TS4.008 | [ ] | POST | /cluster/disconnect_federation |  |
| TS4.009 | [ ] | GET | /cluster/dummy_node_list_node |  |
| TS4.010 | [ ] | GET | /cluster/federations |  |
| TS4.011 | [ ] | POST | /cluster/federations |  |
| TS4.012 | [ ] | DELETE | /cluster/federations/{federation_id} |  |
| TS4.013 | [ ] | GET | /cluster/federations/{federation_id} |  |
| TS4.014 | [ ] | PUT | /cluster/federations/{federation_id} |  |
| TS4.015 | [ ] | POST | /cluster/federations/{federation_id}/disconnect |  |
| TS4.016 | [ ] | POST | /cluster/federations/{federation_id}/pair |  |
| TS4.017 | [ ] | POST | /cluster/federations/{federation_id}/ping |  |
| TS4.018 | [ ] | GET | /cluster/get_hostname |  |
| TS4.019 | [ ] | GET | /cluster/get_running_nodes |  |
| TS4.020 | [ ] | GET | /cluster/hardware |  |
| TS4.021 | [ ] | POST | /cluster/hardware |  |
| TS4.022 | [ ] | GET | /cluster/hardware/{hardware_id} |  |
| TS4.023 | [ ] | POST | /cluster/location |  |
| TS4.024 | [ ] | GET | /cluster/location/{location_id} |  |
| TS4.025 | [ ] | PUT | /cluster/location/{location_id} |  |
| TS4.026 | [ ] | GET | /cluster/locations |  |
| TS4.027 | [ ] | GET | /cluster/node/{node_id} |  |
| TS4.028 | [ ] | GET | /cluster/nodes |  |
| TS4.029 | [ ] | POST | /cluster/pair_federation |  |
| TS4.030 | [ ] | POST | /cluster/refresh_hardware |  |
| TS4.031 | [ ] | GET | /cluster/runtime_config |  |

## (TS5) CONFIG

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS5.001 | [ ] | GET | /config/routing |  |
| TS5.002 | [ ] | PATCH | /config/routing |  |

## (TS6) EMBEDDING

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS6.001 | [ ] | POST | /embedding/batch |  |
| TS6.002 | [ ] | POST | /embedding/chunk |  |
| TS6.003 | [ ] | POST | /embedding/generate |  |
| TS6.004 | [ ] | GET | /embedding/generate/{text} |  |
| TS6.005 | [ ] | GET | /embedding/health |  |
| TS6.006 | [ ] | GET | /embedding/providers |  |

## (TS7) GUIDE

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS7.001 | [ ] | GET | /guide/ |  |
| TS7.002 | [ ] | POST | /guide/import |  |
| TS7.003 | [ ] | POST | /guide/refresh |  |

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
| TS10.001 | [x] | GET | /model_configs/ | test_serving_workflow.py::test_deploy_qwen_and_infer_with_strip_thinking (models.get_model_configs) |
| TS10.002 | [x] | POST | /model_configs/ | test_serving_workflow.py::test_deploy_qwen_and_infer_with_strip_thinking (models.create_model_config) |
| TS10.003 | [x] | DELETE | /model_configs/{model_config_id} | test_serving_workflow.py::test_deploy_qwen_and_infer_with_strip_thinking (models.delete_model_config) |
| TS10.004 | [ ] | GET | /model_configs/{model_config_id} |  |
| TS10.005 | [ ] | PUT | /model_configs/{model_config_id} |  |

## (TS11) MODEL_FILES

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS11.001 | [ ] | GET | /model_files/ |  |
| TS11.002 | [ ] | POST | /model_files/ |  |
| TS11.003 | [x] | GET | /model_files/download_status/ | conftest.py::_ensure (models.wait_for_download) |
| TS11.004 | [ ] | DELETE | /model_files/downloads/cancel_all |  |
| TS11.005 | [ ] | POST | /model_files/search/ |  |
| TS11.006 | [ ] | DELETE | /model_files/{model_file_id} |  |
| TS11.007 | [ ] | GET | /model_files/{model_file_id} |  |
| TS11.008 | [ ] | DELETE | /model_files/{model_file_id}/download |  |
| TS11.009 | [ ] | GET | /model_files/{model_file_id}/memory_usage |  |

## (TS12) MODELS

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS12.001 | [x] | GET | /models/ | conftest.py::_ensure (models.get_model_by_repo_id) |
| TS12.002 | [ ] | POST | /models/ |  |
| TS12.003 | [ ] | POST | /models/cleanup_stale_deployments |  |
| TS12.004 | [ ] | POST | /models/deploy_after_download/{model_key} |  |
| TS12.005 | [x] | POST | /models/download/ | conftest.py::_ensure (models.initiate_model_download); test_models_live.py::test_live_model_metadata_and_download (direct); test_serving_workflow.py::_ensure_model_cached (direct) |
| TS12.006 | [ ] | POST | /models/download_and_deploy |  |
| TS12.007 | [ ] | GET | /models/pending_deployments |  |
| TS12.008 | [ ] | POST | /models/search/ |  |
| TS12.009 | [ ] | DELETE | /models/{model_id} |  |
| TS12.010 | [x] | GET | /models/{model_id} | test_models_live.py::test_live_model_metadata_and_download (models.get_model) |
| TS12.011 | [ ] | GET | /models/{model_id}/configs |  |
| TS12.012 | [ ] | GET | /models/{model_id}/deployment_info |  |
| TS12.013 | [ ] | GET | /models/{model_id}/memory_usage |  |

## (TS13) NEWS

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS13.001 | [ ] | GET | /news/latest |  |
| TS13.002 | [ ] | GET | /news/quadrants |  |

## (TS14) NODE

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS14.001 | [ ] | GET | /node/node_id |  |
| TS14.002 | [ ] | GET | /node/node_status |  |

## (TS15) PING

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS15.001 | [ ] | GET | /ping |  |

## (TS16) PROMPTS

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS16.001 | [ ] | POST | /prompts/elements/ |  |
| TS16.002 | [ ] | GET | /prompts/elements/{element_id} |  |
| TS16.003 | [ ] | POST | /prompts/roles/ |  |
| TS16.004 | [ ] | GET | /prompts/roles/{role_id} |  |
| TS16.005 | [ ] | POST | /prompts/systems/ |  |
| TS16.006 | [ ] | GET | /prompts/systems/{system_id} |  |
| TS16.007 | [ ] | POST | /prompts/templates/ |  |
| TS16.008 | [ ] | GET | /prompts/templates/{template_id} |  |

## (TS17) RETRIEVAL

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS17.001 | [x] | POST | /retrieval/jobs | test_catalog_ingest_retrieval.py::test_s3_ingest_and_retrieve_grpc (direct); test_catalog_ingest_retrieval.py::test_s3_ingest_and_retrieve_inline (direct); test_catalog_multi_source.py::_run_inline_retrieval (direct); test_catalog_multi_source.py::_run_sse_retrieval (direct); test_catalog_multi_source.py::test_catalog_file_ingestion_metadata (direct); test_catalog_multi_source.py::test_catalog_postgres_ingestion_metadata (direct); test_catalog_multi_source.py::test_catalog_slack_ingestion_metadata (retrieval.slack_messages) |
| TS17.002 | [ ] | GET | /retrieval/jobs/{job_id} |  |
| TS17.003 | [x] | GET | /retrieval/jobs/{job_id}/stream | test_catalog_multi_source.py::_run_sse_retrieval (direct) |

## (TS18) SECURITY

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS18.001 | [ ] | POST | /security/consent/accept |  |
| TS18.002 | [ ] | GET | /security/embed.js |  |
| TS18.003 | [ ] | GET | /security/public/config |  |

## (TS19) SERVING

| Test Id | Coverage | Method | Path | Tests |
| --- | --- | --- | --- | --- |
| TS19.001 | [x] | POST | /serving/deploy_model | test_cli_live.py::test_cli_login_and_pat_flow (CLI serve deploy); test_serving_workflow.py::test_deploy_qwen_and_infer_with_strip_thinking (serving.deploy_model) |
| TS19.002 | [x] | DELETE | /serving/deployment/{deployment_id} | test_cli_live.py::test_cli_login_and_pat_flow (serving.stop_deployment); test_serving_workflow.py::test_deploy_qwen_and_infer_with_strip_thinking (serving.stop_deployment) |
| TS19.003 | [x] | GET | /serving/deployment/{deployment_id} | test_cli_live.py::test_cli_login_and_pat_flow (CLI --wait); test_serving_workflow.py::test_deploy_qwen_and_infer_with_strip_thinking (serving.wait_for_deployment) |
| TS19.004 | [x] | GET | /serving/deployment/{deployment_id}/logs | test_serving_workflow.py::_sample_logs (serving.stream_deployment_logs) |
| TS19.005 | [ ] | GET | /serving/deployment/{deployment_id}/logs/patterns |  |
| TS19.006 | [ ] | GET | /serving/deployment/{deployment_id}/status |  |
| TS19.007 | [ ] | GET | /serving/deployments |  |
| TS19.008 | [ ] | POST | /serving/estimate_model_vram |  |
| TS19.009 | [ ] | GET | /serving/health |  |
| TS19.010 | [ ] | GET | /serving/logs/{engine_type} |  |
| TS19.011 | [ ] | GET | /serving/model_instance/{instance_id} |  |
| TS19.012 | [ ] | GET | /serving/model_instances |  |
| TS19.013 | [ ] | POST | /serving/start |  |
| TS19.014 | [ ] | GET | /serving/status |  |

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
| TS21.001 | [ ] | GET | /vectordb/ |  |
| TS21.002 | [ ] | POST | /vectordb/ |  |
| TS21.003 | [ ] | GET | /vectordb/collections |  |
| TS21.004 | [ ] | DELETE | /vectordb/collections/{collection_name} |  |
| TS21.005 | [ ] | POST | /vectordb/insert_vectors |  |
| TS21.006 | [ ] | POST | /vectordb/search_vectors |  |
| TS21.007 | [ ] | DELETE | /vectordb/{vectordb_id} |  |
| TS21.008 | [ ] | GET | /vectordb/{vectordb_id} |  |

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
