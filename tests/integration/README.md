# Integration tests

Live tests that require a running Kamiwaza deployment. Gated by markers so
contributor PRs without a live cluster don't see false reds.

## Environment

| Env var | Purpose | Default |
|---|---|---|
| `KAMIWAZA_BASE_URL` | Primary cluster base URL (must end with `/api`) | `https://kamiwaza.test/api` |
| `KAMIWAZA_API_KEY` | API key for the primary cluster | unset |
| `KAMIWAZA_USERNAME` | Username for password-auth fallback | `admin` |
| `KAMIWAZA_PASSWORD` | Password for password-auth fallback | unset (falls back to kz-login) |
| `KAMIWAZA_VERIFY_SSL` | Set `false` for self-signed certs in dev | `true` |
| `KAMIWAZA_PEER_BASE_URL` | Federation peer cluster base URL (ENG-5784) | unset |
| `KAMIWAZA_PEER_API_KEY` | API key on the peer cluster (ENG-5784) | unset |
| `KAMIWAZA_TEST_LLM_REPO` | Explicit required live-test model; must be paired with `KAMIWAZA_TEST_LLM_ENGINE`, overrides inventory selection, and makes readiness/deployment failures fail rather than skip | unset |
| `KAMIWAZA_TEST_LLM_ENGINE` | Explicit required engine (`llamacpp`, `mlx`, or `vllm`); must be paired with `KAMIWAZA_TEST_LLM_REPO` | unset |
| `KAMIWAZA_TEST_LLM_QUANT` | Quantization for the explicit shared target | selected engine's default |
| `KAMIWAZA_TEST_MLX_LLM_REPO` | MLX model used by live model tests (`KAMIWAZA_CONTEXT_MLX_LLM_REPO` is the legacy alias) | `mlx-community/Qwen3-4B-4bit` |
| `KAMIWAZA_TEST_VLLM_LLM_REPO` | vLLM model used by live model tests (`KAMIWAZA_CONTEXT_VLLM_LLM_REPO` is the legacy alias) | `Qwen/Qwen3-0.6B` |
| `KAMIWAZA_TEST_GGUF_LLM_REPO` | llama.cpp model used by live model tests; it must provide `q4_k` weights (`KAMIWAZA_CONTEXT_GGUF_LLM_REPO` is the legacy alias) | `unsloth/Qwen3-4B-Instruct-2507-GGUF` |
| `KAMIWAZA_CONTEXT_LLM_REPO` | Higher-precedence required model override for context tests; readiness/deployment failures fail rather than skip | shared platform target |
| `KAMIWAZA_CONTEXT_LLM_ENGINE` | Higher-precedence engine override for context tests | shared platform target |
| `KAMIWAZA_CONTEXT_LLM_QUANTIZATION` | Quantization override for context tests | shared target, or `q6_k` with an explicit context repo |
| `KAMIWAZA_TEST_DIFFUSION_REPO` | Required Hugging Face image model used for DiffusionEngine validation | `dg845/tiny-random-stable-diffusion` |
| `KAMIWAZA_TEST_DIFFUSION_FAMILY` | Diffusion family passed in the test model config | `sd15` |
| `KAMIWAZA_TEST_DIFFUSION_BACKEND` | Runtime backend (`auto`, CPU, CUDA/NVIDIA, ROCm/AMD, MLX/MPS, or Intel) | `auto` |
| `KAMIWAZA_TEST_DIFFUSION_IMAGE` | Optional cluster-pullable runtime image for container fleets | source-built image |
| `KAMIWAZA_DIFFUSION_CONFIGURE_CLUSTER` | Temporarily add the image to the trusted cluster catalog | `true` for container validation |
| `KAMIWAZA_TEST_DIFFUSION_FAKE` | Explicit control-plane-only mode; real inference is the default proof | `false` |
| `KAMIWAZA_TEST_DIFFUSION_SIZE` | Generated image size | `64x64` |
| `KAMIWAZA_TEST_DIFFUSION_STEPS` | Denoising steps for the live request | `2` |
| `KAMIWAZA_TEST_DIFFUSION_GUIDANCE` | Guidance value for the live request | `1.0` |
| `KAMIWAZA_TEST_DIFFUSION_TIMEOUT` | Deployment and inference timeout in seconds | `900` |
| `KAMIWAZA_SKIP_DIFFUSION` | Explicitly opt out of diffusion validation | unset/false |

For source-based user-space acceptance, source
`scripts/prepare_diffusion_live.sh` before `pytest -m integration`, or run
`make test-diffusion-live`. The default macOS path prepares the host Metal/MPS
runtime. Container backends build and push the current CPU or NVIDIA engine
image on Linux or macOS, using the chainlogin-managed Docker config when
available. Fleet-specific accelerators can supply a cluster-pullable
`KAMIWAZA_TEST_DIFFUSION_IMAGE`. Kubernetes deliberately selects images from
the operator-owned trusted catalog rather than model config, so preparation
temporarily adds the image to `core-config`, rolls the scheduler, and exposes
`cleanup_diffusion_live` to restore it. `make test-diffusion-live` always runs
that cleanup through an exit trap, including after test failure.
`scripts/run_diffusion_live.sh` is the agent-safe entrypoint. With no arguments
it runs the targeted proof; with arguments it passes them to pytest, allowing a
full `-m integration` run to share the same guaranteed cleanup lifecycle.
Targeted runs write a timestamped JUnit file under `/tmp`; set
`KAMIWAZA_DIFFUSION_JUNIT` to choose its path.

Unless the explicit shared target is configured, live model tests select vLLM
for NVIDIA clusters, MLX only when every reported platform is Apple Silicon,
and the GGUF/llama.cpp target otherwise.

### Brokering env vars (capabilities probe + federated job tests)

The capabilities-probe-via-mesh and federated-job-audit-actor tests
require brokering to be active on both clusters. Brokering needs
Keycloak issuer URLs + cluster IDs configured on both sides; the
`kamiwaza-smoke.py federation-pair` script emits
``WARN: brokering not active on either side (KAMIWAZA_KC_* env vars
not set)`` when this is missing.

These two tests will fail with mesh-proxy errors (capabilities) and
job-result-marker errors (audit-actor) on fleet rigs that don't have
brokering wired up. The other four tests (pair, brokered-user-allowlist,
retrieval, unpair) work without brokering.

Full brokering setup is outside the scope of this harness — operators
running this suite against a fleet rig should ensure brokering is
active per the federation-pair runbook before relying on the
mesh-routing tests.

The peer-cluster env vars only activate the two-cluster federation tests
marked `@pytest.mark.requires_two_clusters`. When unset, those tests are
auto-deselected — contributor PRs without peer creds see no false reds.

## Running

```bash
# All live tests against one cluster
make test-live

# DiffusionEngine deployment + real OpenAI Images API inference
make test-diffusion-live

# Manual equivalent; restore the temporary cluster catalog entry afterward
source scripts/prepare_diffusion_live.sh
uv run pytest -m "integration and live and diffusion" \
  tests/integration/test_diffusion_live.py -v --tb=short
cleanup_diffusion_live

# Full integration suite without diffusion (explicit opt-out only)
uv run pytest -m integration --skip-diffusion -v --tb=short

# Two-cluster federation walkthrough (requires both clusters reachable)
KAMIWAZA_BASE_URL=https://lyra.example/api \
KAMIWAZA_API_KEY=... \
KAMIWAZA_PEER_BASE_URL=https://orion.example/api \
KAMIWAZA_PEER_API_KEY=... \
  uv run pytest -m "requires_two_clusters" tests/integration/test_federation_two_cluster_live.py -v
```

## Marker reference

| Marker | What it covers | Skip behavior |
|---|---|---|
| `live` | Tests that talk to a running Kamiwaza deployment | always selected when running `-m live` |
| `diffusion` | Deploys DiffusionEngine, checks SDK routing, generates base64 PNGs, and validates the unsupported response-mode error | runs and fails closed by default; skipped only with `--skip-diffusion` or `KAMIWAZA_SKIP_DIFFUSION=1` |
| `requires_embedding_model` | Live tests that need a platform embedding deployment that can generate embeddings | auto-provisioned by `embedding_model_prerequisite`, then probed by `embedding_test_target`; skipped if provisioning or the functional probe fails, and harness-provisioned failed deployments are stopped before skip |
| `requires_two_clusters` | Live tests that need a federation peer cluster (ENG-5784) | auto-deselected at collection when `KAMIWAZA_PEER_BASE_URL` is unset; skipped at run time with an explicit reason when peer URL is set but `KAMIWAZA_PEER_API_KEY` is missing (partial-creds case) |

## Adding a federation-aware integration test

1. Add **all four** markers — the canonical walkthrough uses each
   for a distinct purpose, and the suite is broken without any one:

   ```python
   pytestmark = [
       pytest.mark.integration,        # included by CI's -m "integration and live" selector
       pytest.mark.live,               # requires a running Kamiwaza deployment
       pytest.mark.withoutresponses,   # disables pytest-responses HTTP stubbing (real network only)
       pytest.mark.requires_two_clusters,  # auto-deselected when peer creds unset
   ]
   ```

   Missing `integration` makes the suite invisible to the CI selector.
   Missing `withoutresponses` lets `pytest-responses` stub the real
   HTTP calls — the test passes against a mocked stack rather than
   the live peer cluster (the exact defect R2 fixed).

2. Depend on the peer-cluster fixtures from `tests/integration/conftest.py`:

   - `live_kamiwaza_session_client` — the primary cluster's
     `KamiwazaClient`, **session-scoped**. Use this from
     module-scoped fixtures. The older `live_kamiwaza_client` is
     function-scoped — depending on it from a module-scoped fixture
     raises `ScopeMismatch` at fixture resolution (R1 ScopeMismatch
     defect).
   - `live_kamiwaza_peer_client` — the peer cluster's `KamiwazaClient`,
     session-scoped.

   Scope rule: `function-scoped fixtures must not be depended on by
   broader-scoped (module/session) fixtures` is the pytest-builtin
   rule that R1 violated. When in doubt, use the session-scoped
   client; tests that need a fresh per-test client can wrap it in a
   function-scoped fixture instead.

3. Keep teardown best-effort — federation state survives test failures
   and the next run gets a fresh per-run unique federation name + PSK.

4. Mint a fresh PSK *inside* each pair fixture rather than sharing one
   at module scope. The backend resolves the receiver-side federation
   by PSK match; two coexisting pairs sharing a PSK confuse the
   resolver (R5 H1 defect).

See `test_federation_two_cluster_live.py` for the canonical walkthrough
(pair → brokered user → federated job → retrieval smoke → unpair).
