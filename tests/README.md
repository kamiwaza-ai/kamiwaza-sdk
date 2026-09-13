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

Diffusion coverage is included in `pytest -m integration` by default. The
portable fail-closed lane deploys `dg845/tiny-random-stable-diffusion`; the
accelerated lane deploys Qwen Image and Qwen Image Edit, including a real
source PNG and mask; and a two-NVIDIA-GPU lane deploys the Qwen DFloat11 target
and requires an API-observable split device map. Qwen runs on an Apple Silicon
runner or reported NVIDIA/AMD capacity. The split lane skips with the standard
capability reason unless the cluster reports at least two NVIDIA GPUs. Use
`--skip-diffusion` or `KAMIWAZA_SKIP_DIFFUSION=1` only as an explicit operator
choice.

`make test-diffusion-live` first sources `scripts/prepare_diffusion_live.sh`.
On macOS the default `auto`/Metal path creates the checked-out platform's
user-space `diffusion-venv` and verifies MPS. Explicit CPU or NVIDIA backends on
macOS, and the auto-detected NVIDIA or CPU backend on Linux, build the current
runtime source, push it to the KZUAT registry, and export the cluster-pullable image. Set
`KAMIWAZA_PLATFORM_ROOT` when the platform checkout is not the SDK's sibling.
An explicit `KAMIWAZA_TEST_DIFFUSION_IMAGE` remains available for ROCm, Intel,
Spark, or fleet-specific images. Container builds automatically use the
chainlogin-managed Docker config when present; override it with
`KAMIWAZA_DIFFUSION_DOCKER_CONFIG`. Kubernetes selects images from its trusted
operator catalog rather than model configuration, so preparation temporarily
installs the selected image into `core-config` and rolls the scheduler.
`make test-diffusion-live` restores the prior catalog through an exit trap,
including after failure. Manual runs must call `cleanup_diffusion_live`.
Agents can use `scripts/run_diffusion_live.sh` directly; extra arguments replace
the targeted pytest defaults, so passing `-m integration ...` runs the full
suite inside the same prepare/cleanup lifecycle. Each run advertises a
timestamped `/tmp/kzsdk-diffusion-evidence-*` directory. It contains generated
PNGs (plus masked-edit source and mask PNGs) and a redacted JSON manifest with
model/runtime identity, request controls, dimensions, and SHA-256 hashes.

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

## Explicit tenant NVIDIA qualification

`test_tenant_nvidia_live.py` is the strict API lifecycle lane for an
owner-qualified NVIDIA whole-device or MIG profile. It does not query cluster
inventory or import models. First publish the signed owner profile/recipe and
prepare the exact model file through the supported owner/catalog workflow.
Replace the example UUIDs below with that catalog entry's IDs, and use the
owner's actual profile name and qualified minimum memory:

```bash
export KAMIWAZA_TENANT_NVIDIA_REQUEST='{
  "m_id": "00000000-0000-0000-0000-000000000001",
  "m_config_id": "00000000-0000-0000-0000-000000000002",
  "m_file_id": "00000000-0000-0000-0000-000000000003",
  "engine_name": "llamacpp",
  "inferenceResources": {
    "schemaVersion": 1,
    "accelerator": {
      "capability": "gpu", "count": 1,
      "memory": {"minimum": "4Gi"},
      "isolation": "any-qualified", "profile": "nvidia-whole"
    },
    "runtime": {"selection": "automatic"},
    "alternatives": []
  }
}'
# Supply KAMIWAZA_API_KEY or KAMIWAZA_USERNAME/KAMIWAZA_PASSWORD securely.
uv run pytest tests/integration/test_tenant_nvidia_live.py -v \
  --live-base-url https://tenant.example.com/api \
  --junitxml=/tmp/tenant-nvidia-junit.xml -o junit_family=xunit1
```

For MIG, select the signed partition profile (for example
`nvidia-mig-1g.24gb`) and its qualified model/config/file and memory request.
The profile name itself does not prove NVIDIA device injection. Use a trusted
CA bundle for TLS; `KAMIWAZA_VERIFY_SSL=false` is only for self-signed dev/test
clusters.

Only an unset request skips this lane. Once configured, invalid input,
authentication, deployment, readback, invocation, or cleanup errors fail it.
The test requires `DEPLOYED` with instances, unchanged inference-resource
readback, nonempty cold and warm chat responses, and `STOPPED` with zero API
instances within 90 seconds. It records the deployment ID and owner profile
in JUnit properties. Readiness is bounded to 600 seconds; each chat call is
bounded to 60 seconds with no retries.

This is **not** the full qualification matrix or a restricted-platform-user
audit. Separately capture the source/Core/runtime image digests, signed
catalog digest/profile identity, Kubernetes/driver/device-plugin identities,
tenant no-Node RBAC, actual Pod resource grants and device visibility, and
eventual absence of labeled Kubernetes resources. The owner-side observer
must remain separate from the tenant SDK identity. Replacement, restart,
negative paths, and pre-1.34 acceptance still require their own evidence.

## Shared Fixtures
- `dummy_client` – lightweight HTTP stub for unit tests (records calls, replays canned responses).
- `client_factory` – builds real `KamiwazaClient` instances with consistent defaults.
- `deployable_model_target` – platform-compatible, overrideable model repository,
  engine, and quantization used by the deployability probe, serving workflow,
  and CLI deployment test.
- `ingestion_environment` – spins up the MinIO docker stack and seeds sample parquet data for ingest/retrieval tests.
- `live_kamiwaza_client` – asserts a live server is reachable (`/ping`), then authenticates using either `KAMIWAZA_API_KEY` or username/password credentials.

Artifacts that need disk (model downloads, fixtures) should use the `artifact_cache_dir` fixture to avoid polluting the repo. Tests that require real network access add the `withoutresponses` marker so the `pytest-responses` plugin does not stub out HTTP calls.
