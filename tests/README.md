# Kamiwaza SDK Test Harness

M0 establishes the shared pytest scaffolding so every feature ships with deterministic unit coverage, docker-backed integration checks, and opt-in live/e2e smoke tests.

> **Naming note:** Install the SDK via `pip install kamiwaza-sdk` and import it as `kamiwaza_sdk`. The legacy `kamiwaza_client` module name still works via a compatibility shim so older tests don't break, but new suites should stick to `kamiwaza_sdk`.

## Markers & Layers
- `unit` – fast, deterministic tests with no external services.
- `contract` – schema/fixture verification against recorded API responses.
- `integration` – exercises local dependencies (Docker/MinIO, seeded fixtures).
- `live` – talks to a running Kamiwaza deployment (defaults to `https://localhost/api`).
- `diffusion` – deploys a DiffusionEngine and generates real images through the SDK's OpenAI-compatible client.
- `e2e` – multi-step workflows spanning ingest → catalog → retrieval, typically live.

Enable strict marker checking via `pytest.ini`, so new suites must opt into at least one layer.

## Running the Suites
```bash
# Unit only (default recommendation on PRs)
pytest -m unit

# Contract tests (future milestone)
pytest -m contract

# Integration (local/dependency-focused): excludes live deployment suites.
# Some tests still need Docker (MinIO/catalog fixtures) and internet (artifact downloads).
# If Docker daemon is unavailable, set KAMIWAZA_DOCKER_HOST/DOCKER_HOST to a Podman socket.
pytest -m "integration and not live"

# Live smoke tests (needs running Kamiwaza server)
pytest -m "integration and live" --live-base-url https://localhost/api --live-username admin --live-password kamiwaza

# Run only the cross-platform DiffusionEngine proof.
make test-diffusion-live

# Explicitly omit diffusion from a full integration run.
pytest -m integration --skip-diffusion
```

`--live-base-url`, `--live-api-key`, `--live-username`, and `--live-password` override the defaults pulled from `KAMIWAZA_BASE_URL`, `KAMIWAZA_API_KEY`, `KAMIWAZA_USERNAME`, and `KAMIWAZA_PASSWORD`. When no API key is provided the fixtures fall back to password auth (defaulting to `admin` / `kamiwaza`, which may not match your local deployment). Live/integration tests automatically skip when container runtime access, server health, or credentials are missing, so CI can include them as optional jobs.

Some live integration tests exercise admin-only mutation paths. For those tests, prefer supplying an admin-scoped PAT via `KAMIWAZA_API_KEY` instead of relying on the default session PAT minted from username/password bootstrap.

Diffusion coverage is fail-closed and included in `pytest -m integration` by
default. It deploys `hf-internal-testing/tiny-stable-diffusion-pipe`, performs
real image inference, validates base64 PNG output and cleans up its deployment
and config. Use `--skip-diffusion` or `KAMIWAZA_SKIP_DIFFUSION=1` only as an
explicit operator choice; missing runtime images, host dependencies or model
access are test failures rather than silent capability skips.

The inference tests choose one model/engine/quantization target from the live
cluster inventory. NVIDIA selects vLLM, complete Apple Silicon inventory selects
MLX, and CPU-only, mixed, or incomplete inventory selects llamacpp/GGUF. Override
the entire target explicitly with `KAMIWAZA_TEST_LLM_REPO` plus
`KAMIWAZA_TEST_LLM_ENGINE` and optional `KAMIWAZA_TEST_LLM_QUANT`; this takes
precedence over hardware selection and is the fleet/topology integration seam.
An explicit target is required: download or deployment failure fails the suite
instead of being reported as a capability skip.
Override only the automatically selected repositories with `KAMIWAZA_TEST_MLX_LLM_REPO`,
`KAMIWAZA_TEST_VLLM_LLM_REPO`, or `KAMIWAZA_TEST_GGUF_LLM_REPO`. Context tests
also accept `KAMIWAZA_CONTEXT_LLM_REPO`, `KAMIWAZA_CONTEXT_LLM_ENGINE`, and
`KAMIWAZA_CONTEXT_LLM_QUANTIZATION`; the latter defaults to `q6_k` for an
explicit context repository and otherwise inherits the shared target. An
explicit context repository is also required and fails closed.

## Shared Fixtures
- `dummy_client` – lightweight HTTP stub for unit tests (records calls, replays canned responses).
- `client_factory` – builds real `KamiwazaClient` instances with consistent defaults.
- `deployable_model_target` – platform-compatible, overrideable model repository,
  engine, and quantization used by the deployability probe, serving workflow,
  and CLI deployment test.
- `ingestion_environment` – spins up the MinIO docker stack and seeds sample parquet data for ingest/retrieval tests.
- `live_kamiwaza_client` – asserts a live server is reachable (`/ping`), then authenticates using either `KAMIWAZA_API_KEY` or username/password credentials.

Artifacts that need disk (model downloads, fixtures) should use the `artifact_cache_dir` fixture to avoid polluting the repo. Tests that require real network access add the `withoutresponses` marker so the `pytest-responses` plugin does not stub out HTTP calls.
