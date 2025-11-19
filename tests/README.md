# Kamiwaza SDK Test Harness

M0 establishes the shared pytest scaffolding so every feature ships with deterministic unit coverage, docker-backed integration checks, and opt-in live/e2e smoke tests.

> **Naming note:** Install the SDK via `pip install kamiwaza-sdk` and import it as `kamiwaza_sdk`. The legacy `kamiwaza_client` module name still works via a compatibility shim so older tests don't break, but new suites should stick to `kamiwaza_sdk`.

## Markers & Layers
- `unit` – fast, deterministic tests with no external services.
- `contract` – schema/fixture verification against recorded API responses.
- `integration` – exercises local dependencies (Docker/MinIO, seeded fixtures).
- `live` – talks to a running Kamiwaza deployment (defaults to `https://localhost/api`).
- `e2e` – multi-step workflows spanning ingest → catalog → retrieval, typically live.

Enable strict marker checking via `pytest.ini`, so new suites must opt into at least one layer.

## Running the Suites
```bash
# Unit only (default recommendation on PRs)
pytest -m unit

# Contract tests (future milestone)
pytest -m contract

# Integration: requires Docker + seeded MinIO fixture
pytest -m integration

# Live smoke tests (needs running Kamiwaza server)
pytest -m live --live-base-url https://localhost/api --live-username admin --live-password kamiwaza
```

`--live-base-url`, `--live-api-key`, `--live-username`, and `--live-password` override the defaults pulled from `KAMIWAZA_BASE_URL`, `KAMIWAZA_API_KEY`, `KAMIWAZA_USERNAME`, and `KAMIWAZA_PASSWORD`. When no API key is provided the fixtures fall back to password auth (defaulting to `admin` / `kamiwaza`). Live/integration tests automatically skip when Docker, server health, or credentials are missing, so CI can include them as optional jobs.

## Shared Fixtures
- `dummy_client` – lightweight HTTP stub for unit tests (records calls, replays canned responses).
- `client_factory` – builds real `KamiwazaClient` instances with consistent defaults.
- `qwen_model_id` – canonical `mlx-community/Qwen3-4B-4bit` identifier for download/deploy tests; keep plumbing ready for a GGUF mirror.
- `ingestion_environment` – spins up the MinIO docker stack and seeds sample parquet data for ingest/retrieval tests.
- `live_kamiwaza_client` – asserts a live server is reachable (`/ping`), then authenticates using either `KAMIWAZA_API_KEY` or username/password credentials.

Artifacts that need disk (model downloads, fixtures) should use the `artifact_cache_dir` fixture to avoid polluting the repo. Tests that require real network access add the `withoutresponses` marker so the `pytest-responses` plugin does not stub out HTTP calls.
