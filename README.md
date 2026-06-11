# Kamiwaza Python SDK

Python client library for interacting with the Kamiwaza AI Platform. This SDK provides a type-safe interface to all Kamiwaza API endpoints with built-in authentication, error handling, and resource management.

## Installation

```bash
pip install kamiwaza-sdk
```

> **Naming note:** Install the package as `kamiwaza-sdk`, but import it as `kamiwaza_sdk`. A deprecated `kamiwaza_client` alias remains for older snippets, though new code should prefer `kamiwaza_sdk`.

> **Version compatibility:** This SDK (version 0.5.1+) is incompatible with Kamiwaza versions before 0.5.1. Please ensure you're using the latest version of Kamiwaza.

## Python SDK Usage

```python
from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.authentication import UserPasswordAuthenticator
from kamiwaza_sdk.schemas.auth import PATCreate

client = KamiwazaClient("https://localhost/api")

# Option 1 (recommended): Personal Access Token
# export KAMIWAZA_API_KEY=<your-pat>
# client automatically loads the token and reuses it for every request.

# Option 2: bootstrap with username/password to mint a PAT
client.authenticator = UserPasswordAuthenticator("admin", "kamiwaza", client.auth)
pat = client.auth.create_pat(PATCreate(name="local-bootstrap")).token
print("Save this token:", pat)
```

## Federation walkthrough (kamiwaza-mesh-v1.0.0)

> Available in **kamiwaza-sdk 1.0.0+** under the new top-level
> `kamiwaza` namespace, distinct from the legacy `kamiwaza_sdk`
> namespace above. See the design's §4.2.11 for the full surface.

The new `kamiwaza` namespace ships the federation-aware client. The
eight-step demo author's `setup.py` flow uses **only SDK calls** — no
`kubectl exec`, no manual SQL, no Keycloak admin REST:

1. **Pair LYRA with ORION** — `kz.federations.pair(...)`
2. **Seed personas** — `kz.subjects.upsert(...)` (replaces the v0.1.x
   two-phase KC recipe — see authoring-guide §6)
3. **Bind the cluster execution gate** — `kz.cluster.set_execution_gate(...)`
   (replaces the `kubectl exec` recipe — see authoring-guide §9.1)
4. **Register the dataset** — `kz.datasets.create(...)`
5. **Bind the dataset's attribute gate** — `kz.datasets.set_gate(...)`
6. **Allowlist the brokered user + grant viewer** —
   `kz.federations["ORION"].users.add(...)` plus
   `kz.subjects.grants("user").create(...)`
7. **Submit the federated job** — `kz.jobs.run(...)`
8. **Observe the audit trail** — `kz.cluster.operations()` / receiver-side
   `gate_binding{,_set,clear}` + `subject_upsert` audit events

### Configure the client

```python
import os
from kamiwaza import Kamiwaza

# Environment-driven config (recommended)
os.environ.setdefault("KAMIWAZA_BASE_URL", "https://lyra.kamiwaza.test")
os.environ.setdefault("KAMIWAZA_TOKEN", "<personal-access-token>")
kz = Kamiwaza.from_env()

# Or pass explicit values
kz = Kamiwaza(
    base_url="https://lyra.kamiwaza.test",
    token="<personal-access-token>",
)

# Use as a context manager so the underlying httpx transport is
# released cleanly:
with Kamiwaza.from_env() as kz:
    ...  # walkthrough below
```

### Step 1 — Pair LYRA with ORION

The initiator drives the handshake. The receiver only needs the
PSK propagated through DataHub plus its admin baseline ReBAC tuple
(install-dev.sh seeds those automatically).

```python
fed = kz.federations.pair(
    name="ORION",
    role="initiator",
    remote_url="https://orion.kamiwaza.test",
    remote_admin_token="<orion-admin-pat>",  # initiator-only
)
print(fed.id, fed.status)  # e.g. fed-orion-… PAIRED
```

If DataHub PSK propagation is mid-flight when the request lands on
the receiver, the SDK retries with exponential backoff until the
server's structured 503 (`detail.reason == "psk_propagation_timeout"`)
times out the budget — see `kamiwaza.exceptions.FederationPairTimeoutError`.

### Step 2 — Declare the attribute vocabulary, then seed personas (M3 + M3.1)

Replaces the v0.1.x two-phase Keycloak admin recipe (see
`authoring-a-federated-demo.md` §6). Two sub-steps:

**Step 2a — Declare the realm's attribute vocabulary (M3.1, v0.3.6).**
Every attribute name a subject can hold must be declared in the realm
BEFORE `kz.subjects.upsert(...)` writes it. Keycloak's realm default
`unmanagedAttributePolicy=None` silently drops attribute writes for
undeclared names — the M3.1 declared-vocabulary surface converts that
silent drop into a 400 with structured remediation, so unknown names
fail loudly at upsert time instead of returning success with empty
attributes.

```python
kz.cluster.declare_attribute("clearance", type="string")
kz.cluster.declare_attribute("country",   type="string")
kz.cluster.declare_attribute("programs",  type="string[]")  # multivalued
```

`declare_attribute` is idempotent on identical shape — safe to re-run
during demo setup. Shape change on a declared-state attribute returns
400 `shape_change_on_declared`; deprecate + withdraw first to retire
the old shape. See `kz.cluster.list_attributes()` to inspect the
current vocabulary, and `kz.cluster.deprecate_attribute(name)` /
`kz.cluster.withdraw_attribute(name, force=True)` for retirement.

For PII-grade attributes the gate consumes via the mesh-envelope
`user_attrs` channel (not as a JWT claim), pass `sensitive=True`:

```python
kz.cluster.declare_attribute("ssn_last4", type="string", sensitive=True)
```

For attributes attested by a peer cluster's brokered-user provisioning
(rather than set by local admin), pass `authority="mesh_peer"` —
local admin attempts to set these on local users return 400
`wrong_authority_for_subject`. Defaults (`sensitive=False`,
`authority="local_admin"`) match the demo flow's normal case.

**Step 2b — Seed personas.** The single PUT writes attributes in one
round-trip, infers multivalued KC entries for list-shaped values, and
rolls back attribute deltas on partial failure (T3.4).

```python
cdr_baker = kz.subjects.upsert(
    "cdr-baker",
    attributes={
        "clearance": "TS",
        "country": "USA",
        "programs": ["IRIS", "ARGOS"],   # list → multivalued KC attribute
    },
    password="cdr-baker",
)
print(cdr_baker.id, cdr_baker.attributes["clearance"])  # kc-uuid TS
```

Audit emits `subject_upsert{outcome=success}` on the receiver. A
partial-failure rollback emits `subject_upsert_rollback{outcome=...}`
so operators can spot drift cases in logs (T3.7). Attempting `upsert`
with an undeclared attribute name returns 400 `attribute_not_registered`
with the undeclared names enumerated and remediation text pointing
back at `declare_attribute(...)`.

### Step 3 — Bind the cluster execution gate (M3)

Replaces the `kubectl exec ... RuntimeConfig().set_config(...)` recipe
(see `authoring-a-federated-demo.md` §9.1). The PUT validates the
classpath is an ExecutionGate subclass and validates `config` against
the gate's `config_schema()` before persisting (T2.6 jsonschema).

```python
binding = kz.cluster.set_execution_gate(
    type="kamiwaza.services.authz.gates.default_gates.AllowAllExecutionGate",
    # config={} omitted — AllowAllExecutionGate declares no config_schema()
)
print(binding.gate_name, binding.kind)  # allow_all_execution_gate execution
```

Without an active binding, mesh job submission fails with `403
no_execution_gate_configured_for_mesh`. The SDK surfaces wrong-kind
attempts (binding an `AttributeGate` as an execution gate) as
`KamiwazaError` with status 400.

### Step 4 — Register the dataset (M3)

```python
conjunctions = kz.datasets.create(
    name="conjunctions",
    platform="postgres",
    environment="PROD",
    properties={
        "connection_secret_urn": "urn:li:secret:postgres-conjunctions",
        "table": "public.conjunctions",
    },
)
print(conjunctions.urn)  # urn:li:dataset:(postgres,conjunctions,PROD)
```

### Step 5 — Bind the dataset's attribute gate (M3)

```python
ds_binding = kz.datasets.set_gate(
    conjunctions.urn,
    type="kamiwaza_extensions.classified_conjunction_gate.ClassifiedConjunctionGate",
    config={
        "classification_field": "classification",
        "releasable_to_field": "releasable_to",
        "program_compartment_field": "program_compartment",
    },
)
print(ds_binding.dataset_urn, ds_binding.gate_name)
```

The server verifies the classpath is an `AttributeGate` (wrong-kind →
400) and that `config` matches the gate's `config_schema()` (mismatch
→ 400 `schema_validation_failed`). Owner-on-dataset ReBAC enforces
that only the dataset's owner can rebind the gate (T2.5 follow-up).

### Step 6 — Allowlist the brokered user + grant viewer (M3)

```python
# Receiver-side allowlist (same as WS-M1):
kz.federations["ORION"].users.add(
    external_id="cdr-baker@lyra-cluster-uuid",
    initial_tuples=[
        {
            "subject": "user:cdr-baker@lyra-cluster-uuid",
            "relation": "viewer",
            "object": "cluster:ORION",
        },
    ],
)

# Then attach a ReBAC viewer relation on the dataset (M3 subjects.grants):
kz.subjects.grants("cdr-baker").create(
    object_namespace="dataset",
    object_id=conjunctions.urn,
    relation="viewer",
)
```

If the user isn't on the allowlist when a mesh request arrives,
ext-authz returns 403 with `detail.reason ==
"brokered_user_not_allowlisted"`. The SDK surfaces that as
`kamiwaza.exceptions.BrokeredUserNotAllowlistedError`.

### Step 7 — Submit the federated job

`target_cluster` is the federation name (the same name used at
pair time). Omit it to run locally on the cluster the SDK is
talking to.

```python
result = kz.jobs.run(
    target_cluster="ORION",
    entrypoint="python /workdir/query.py --rows 1000",
)
print(result.status, result.audit_actor)
# SUCCEEDED  cdr-baker@lyra-cluster-uuid
```

For longer jobs, prefer the async + poll pattern — `submit_async`
returns immediately and `wait` polls with bounded backoff until
the job reaches a terminal state:

```python
job_id = kz.jobs.submit_async(
    target_cluster="ORION",
    entrypoint="python /workdir/long_query.py",
)
result = kz.jobs.wait(job_id, timeout=600)
```

`wait` raises `kamiwaza.exceptions.MeshJobTimeoutError` when the
budget expires before a terminal state. A *failed* job returns a
JobResult with `status="FAILED"` and an `error` message — that's
not exceptional, that's data.

### Step 8 — Observe audit

The receiver-side audit log shows the job completing as the
originating user (`cdr-baker@lyra-cluster-uuid`), not as a
system principal. M3 adds two more event types operators can grep for:

- `gate_binding{action: set|clear, kind: execution|attribute}` — every
  PUT/DELETE on the cluster + dataset gate endpoints (T2.12).
- `subject_upsert{,_rollback}` and `subject_grant_change` — every
  AuthzSubjects mutation (T3.7).

```bash
# Federated job audit trail
kubectl -n kamiwaza logs deployment/core-scheduler \
    | grep federated_job_completed \
    | jq 'select(.audit_actor)'

# M3 gate-binding + subject-management audit
kubectl -n kamiwaza logs deployment/core-scheduler \
    | jq 'select(.event_type | startswith("gate_binding") or
                                  startswith("subject_"))'
```

The `audit_actor` field is the same value `kz.jobs.run(...).audit_actor`
returns in step 7 — that round-trip is the demo gate's load-bearing
signal.

### Recoverable long-jobs

For jobs that may take longer than ~60 seconds, use `kz.jobs.run(...,
recoverable=True)` instead of the default. The default holds the HTTP
connection for the full job duration; FastAPI buffers the
`X-Job-Id` response header along with the body and only flushes both on
completion. If the connection drops mid-job, the SDK never sees the
`X-Job-Id` and has no handle to recover the result from.

`recoverable=True` flips the SDK to a `submit + poll` shape under the
covers:

1. `POST /api/cluster/jobs/submit` returns the `job_id` immediately.
2. The SDK polls `/cluster/jobs/{id}/status` with exponential backoff
   until the job reaches a terminal state.
3. `/cluster/jobs/{id}/result` fetches the final payload.

Because the `job_id` is in the SDK's hands from the first response, a
mid-job process crash is recoverable on a fresh SDK instance:

```python
# Original process
job_id = kz.jobs.submit_async(
    entrypoint="python query.py",
    target_cluster="ORION",
    timeout_seconds=600,
)
# ... persist job_id somewhere (sqlite, /tmp file, etc.) ...
# ... process dies ...

# Fresh process, much later
saved_job_id = load_persisted_job_id()
result = kz.jobs.wait(saved_job_id, timeout=600)
print(result.status, result.result)
```

**Recommended:** use `recoverable=True` for any job with
`timeout_seconds > 60`. The two-call cost (submit + poll) is amortized
over the long runtime.

### Error handling cheat sheet

The SDK maps server-side error contracts to typed exceptions so
customer code can branch on the failure mode. All inherit from
`kamiwaza.exceptions.KamiwazaError`:

| Exception                              | Trigger                                                         |
|----------------------------------------|-----------------------------------------------------------------|
| `FederationPairTimeoutError`           | Receiver couldn't see the PSK before the retry budget expired.  |
| `BrokeredUserNotAllowlistedError`      | Mesh request from a user not in the receiver's allowlist.       |
| `MeshJobTimeoutError`                  | `kz.jobs.wait(...)` budget expired before terminal state.       |
| `MeshJobFailedError`                   | Job reached FAILED state and the caller asked for an exception. |
| `NativeRealmRequiredError`             | Operation requires a native (non-brokered) realm user.          |
| `KamiwazaError` 403 `...read-only...`  | Write aimed at the **Global Workroom** (shared read-only catalog) — by design; write to a workroom you own instead. See [Context Service](docs/services/context/README.md). |
| `KamiwazaError` (catch-all)            | Other 4xx/5xx; check `.status_code` and `.body` for details.    |

```python
from kamiwaza import KamiwazaError
from kamiwaza.exceptions import FederationPairTimeoutError

try:
    kz.federations.pair(name="ORION", role="initiator", remote_url=...)
except FederationPairTimeoutError as exc:
    # Retriable — DataHub propagation didn't make the deadline this run.
    print(f"Retry later: {exc.body!r}")
except KamiwazaError as exc:
    # Catch-all for everything else.
    print(f"{exc.status_code}: {exc}")
```

## Examples

The `/examples` directory contains Jupyter notebooks demonstrating various use cases. To run them locally against a Kamiwaza dev cluster, install JupyterLab in your env (`pip install jupyterlab ipywidgets`) and run `jupyter lab` from `examples/`. Set `KAMIWAZA_BASE_URL` and `KAMIWAZA_API_KEY` in the environment before launching so the notebooks can reach your cluster.

1. [Model Download and Deployment](examples/01_download_and_deploy.ipynb) - A comprehensive guide to searching, downloading, deploying, and using models with the Kamiwaza SDK
2. [Quick Model Deployment](examples/02_download_and_deploy_quick.ipynb) - Streamlined approach to download and deploy models using a single function
3. [Model Evaluation](examples/03_eval_multiple_models.ipynb) - How to evaluate and benchmark multiple language models for performance comparison using the streamlined `download_and_deploy_model` function
4. [Structured Output](examples/04_structured_output.ipynb) - Using Kamiwaza's OpenAI-compatible interface to generate structured outputs with specific JSON schemas
5. [Function Calling](examples/05_tools.ipynb) - Demonstrates how to use function calling (tools) with Kamiwaza's OpenAI-compatible API
6. [Web Agent](examples/06_web-agent.ipynb) - Build an AI agent that can browse and interact with web pages
7. [RAG Demo](examples/07_kamiwaza_rag_demo.ipynb) - Retrieval Augmented Generation using Kamiwaza's vector database and embedding services
8. [App Garden and Tool Shed](examples/08_app_garden_and_tools.ipynb) - Deploy containerized applications and MCP Tool servers
9. [Reference Chatbot App](examples/chatbot-app) - A buildable Kamiwaza extension app that mirrors the default `kz-ext create --type app` starter

More examples coming soon!

## Service Overview

| Service | Description | Documentation |
|---------|-------------|---------------|
| `client.models` | Model management | [Models Service](docs/services/models/README.md) |
| `client.serving` | Model deployment | [Serving Service](docs/services/serving/README.md) |
| `client.vectordb` | Vector database | [VectorDB Service](docs/services/vectordb/README.md) |
| `client.context` | Workroom-scoped vector DBs, ontologies, ingestion, retrieval | [Context Service](docs/services/context/README.md) |
| `client.catalog` | Data management | [Catalog Service](docs/services/catalog/README.md) |
| `client.embedding` | Text processing | [Embedding Service](docs/services/embedding/README.md) |
| `client.retrieval` | Search | [Retrieval Service](docs/services/retrieval/README.md) |
| `client.cluster` | Infrastructure | [Cluster Service](docs/services/cluster/README.md) |
| `client.lab` | Lab environments | [Lab Service](docs/services/lab/README.md) |
| `client.auth` | Security | [Auth Service](docs/services/auth/README.md) |
| `client.authz` | Authorization tuples/checks | [AuthZ Service](docs/services/authz/README.md) |
| `client.activity` | Monitoring | [Activity Service](docs/services/activity/README.md) |
| `client.openai` | OpenAI API compatible| [OpenAI Service](docs/services/openai/README.md) |
| `client.apps` | App deployment | [App Service](docs/services/apps/README.md) |
| `client.tools` | Tool servers (MCP) | [Tool Service](docs/services/tools/README.md) |
| `client.skills` | Skills Library catalog and package workflows | [Skills Service](docs/services/skills/README.md) |
| `client.ingestion` | Data ingestion | [Ingestion Service](docs/services/ingestion/README.md) |
| `client.enclaves` | Connectors + documents | [Enclaves Service](docs/services/enclaves/README.md) |

## Auth / User Management (0.9.0)

- **Base URL rule:** set `base_url=https://<host>` (no `/auth` suffix). Quick preflight: `GET {base_url}/auth/ping` → 200. If you include `/auth`, calls will double-prefix and fail.
- **Admin-only:** creating/resetting users requires an admin bearer.
- **Auth-on semantics:** `create_local_user` provisions Keycloak so the user can authenticate; `reset_user_password` updates Keycloak only (Keycloak is authoritative). Auth-off updates the local hash only.
- **Roles caveat:** requested realm roles must exist; otherwise create will 500 + rollback. Omit roles or use known-good roles.
- **Self-signed TLS:** set `--verify-ssl false` (or `verify_ssl=False`) when needed.
- A runnable smoke script lives at `scripts/fed_user_smoke.py` (see script usage inside).


## Integration Tests

The `tests/integration` suite spins up a MinIO fixture via Docker Compose and
exercises the ingestion → catalog flow using the SDK. Run it with:

```bash
pytest -m integration
```

> **Note:** Retrieval checks are currently marked `xfail` because the live
> deployment returns HTTP 500 while Ray-backed transport is being stabilised.


## Extension Developer Tools (`kz-ext`)

The `kz-ext` CLI helps extension developers scaffold, validate, build, deploy, and debug Kamiwaza extensions.

### Installation

```bash
# Install the SDK (includes extension tools)
pip install kamiwaza-sdk

# Optional extras for publishing and AI-powered conversion:
pip install kamiwaza-sdk[publish]    # Adds boto3 for kz-ext publish
pip install kamiwaza-sdk[convert]    # Adds anthropic for kz-ext convert (OpenAI is included by default)
pip install kamiwaza-sdk[all]        # Both

# Verify
kz-ext --version
```

### Quick Start

```bash
# 1. Authenticate (local dev — uses https://kamiwaza.test/api, skips SSL verify)
kz-ext login --dev
# Or specify a URL:  kz-ext login https://your-instance.example.com/api
# For self-signed certs:  kz-ext login --no-verify-ssl

# 2. Scaffold a new extension
mkdir my-app && cd my-app
kz-ext create --type app --name my-app

# The generated app is already a working AI starter with:
# - explicit model selection
# - a simple chat UI
# - AGENTS.md and CLAUDE.md for coding assistants

# 3. Validate the extension metadata
kz-ext validate

# 4. Run locally with Docker Compose
kz-ext dev local

# 5. Deploy to a Kamiwaza cluster (build, push, deploy — one command)
kz-ext dev

# 6. Iterate: change code, re-run (zero-downtime update via PATCH)
kz-ext dev

# 7. Inspect the running extension
kz-ext status
kz-ext logs --service backend --follow
kz-ext shell --service backend

# 8. Forward a port for direct debugging
kz-ext port-forward --service backend --port 8000

# 9. Convert an existing app to a Kamiwaza extension
kz-ext convert /path/to/existing-app

# 10. Publish to an extension catalog
kz-ext config publish-profile prod \
  --registry ghcr.io/my-org \
  --catalog-endpoint https://my-account.r2.cloudflarestorage.com \
  --catalog-bucket extensions-prod \
  --catalog-credentials aws-profile:prod

kz-ext publish --stage prod
```

### Commands

| Command | Description |
|---------|-------------|
| `kz-ext login [url]` | Authenticate with a Kamiwaza instance (default: `https://kamiwaza.test/api`). Supports `--api-key`, `--name`, `--list`, `--use`, `--no-verify-ssl`. |
| `kz-ext create --type <type> --name <name>` | Scaffold a new extension in the current (empty) directory. Types: `app` (Next.js + FastAPI), `tool` (FastMCP), `service` (minimal). |
| `kz-ext validate [path]` | Validate `kamiwaza.json`, `docker-compose.yml`, and clear platform-runtime incompatibilities such as privileged ports or root-only web containers. Use `--json` for machine-readable output. |
| `kz-ext dev local` | Run the extension locally via Docker Compose with Kamiwaza env vars injected. Auto-detects port conflicts and remaps to available ports. Supports `--sdk-repo`, `--detach`, and `--auth` (bridges the developer's identity from `kz-ext login` and routes loopback Kamiwaza URLs through the host gateway — see [docs/extensions/cli-reference/dev-local.md](docs/extensions/cli-reference/dev-local.md)). |
| `kz-ext dev` | Build, push, and deploy to a Kamiwaza cluster. Uses zero-downtime PATCH updates for existing extensions. Supports `--no-build`, `--no-push`, `--service`, `--revision`, `--sdk-repo`. |
| `kz-ext status` | Show deployment status: phase, per-service readiness, URL, and recent K8s events. Supports `--name`. |
| `kz-ext logs` | Stream logs from deployed extension pods. Supports `--service`, `--follow`, `--tail`, `--name`. |
| `kz-ext shell` | Open an interactive shell in a running extension container. Supports `--service`, `--name`. |
| `kz-ext port-forward` | Forward a port from a deployed pod to localhost for debugging. Supports `--service`, `--port`, `--name`. |
| `kz-ext convert <path>` | AI-powered conversion of existing apps to Kamiwaza extensions. Analyzes code, generates `kamiwaza.json`, and wires in SDK integration. Supports `--dry-run`. |
| `kz-ext publish --stage <profile>` | Build production images, push to registry, and publish to an S3-compatible extension catalog. Supports `--dry-run`, `--force`, `--no-build`, `--no-push`. |
| `kz-ext bump` | Bump extension version in `kamiwaza.json`. Defaults to patch. Supports `--level major\|minor\|patch`. |
| `kz-ext config publish-profile` | Create, list, show, or delete named publish profiles. Supports `--list`, `--show`, `--delete`, `--repo-level`. |
| `kz-ext doctor` | Check your development environment (Python, Docker, Compose, kubectl, connection health, runtime libs). |

### Development Workflow

The typical edit-deploy-test cycle:

1. **`kz-ext dev`** builds Docker images with a unique dev tag, pushes to the cluster registry, and deploys via the platform API.
2. On the **first run**, it creates a new extension (POST). On **subsequent runs**, it patches the existing deployment with new image tags (PATCH), triggering a Kubernetes rolling update with zero downtime.
3. **`kz-ext status`** shows whether the rollout is complete, per-service readiness, and any issues (image pull failures, OOM kills, probe failures).
4. **`kz-ext logs`** and **`kz-ext shell`** give direct access to running pods for debugging.

No version bumps, registry builds, or manual redeploy steps required during development.

### Publishing

When your extension is ready for release:

1. **Configure a publish profile** with `kz-ext config publish-profile` — specify a container registry and S3-compatible catalog endpoint.
2. **Bump the version** with `kz-ext bump` (or `kz-ext bump --level minor`).
3. **`kz-ext publish --stage prod`** builds production-tagged images, pushes to the profile's registry, and publishes to the catalog.
4. **`kz-ext publish --stage prod --dry-run`** previews what would happen without making changes.

Publish profiles support multiple environments (dev/staging/prod) and CI via env var overrides (`KZ_PUBLISH_REGISTRY`, `KZ_PUBLISH_CATALOG_ENDPOINT`, etc.).

### Converting Existing Apps

```bash
kz-ext convert /path/to/existing-app
```

Uses an AI agent to analyze existing Dockerfiles, compose files, and size-capped source context, then generates `kamiwaza.json` and wires in SDK integration (health endpoints, auth middleware, runtime libraries). Common secret-bearing files such as `.env`, credential JSON files, and private key files are excluded from that context. The conversion flow now validates generated output against the Kamiwaza runtime contract as well, including non-root execution, unprivileged HTTP ports, and read-only-root-filesystem-friendly web wrappers. All changes are git-tracked — review with `git diff`.

Optionally uses `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`) for AI-powered conversion. Falls back to basic `kamiwaza.json` generation without an API key. Set `OPENAI_BASE_URL` to use any OpenAI-compatible provider (Kamiwaza, vLLM, Ollama, etc.). Set `KZ_PUBLISH_DOCKER_TOKEN` or `DOCKER_TOKEN` for registry auth during publish.

### Extension Types

- **App** (`--type app`): Full-stack extension with Next.js frontend and FastAPI backend, pre-wired with `@kamiwaza-ai/extensions-lib` and `kamiwaza-extensions-lib`.
- The app starter includes a working authenticated chat flow so developers can customize a real extension instead of starting from a status dashboard.
- **Tool** (`--type tool`): MCP tool server using FastMCP with `kamiwaza-extensions-lib`.
- **Service** (`--type service`): Minimal containerized service.

### Local SDK Development (`--sdk-repo`)

When iterating on the runtime libraries themselves (`kamiwaza-extensions-lib` or `@kamiwaza-ai/extensions-lib`), use `--sdk-repo` to override the published packages with your local SDK source:

```bash
# Via CLI flag
kz-ext dev local --sdk-repo ~/repos/kamiwaza-sdk

# Or via repo-local config (gitignored)
mkdir -p .kz-ext
cat > .kz-ext/local.yaml << 'EOF'
sdk_repo: /Users/you/repos/kamiwaza-sdk
runtime_libs:
  python: local       # override Python lib (default: local when sdk_repo is set)
  typescript: local   # override TypeScript lib
build_typescript: true  # auto-build TS package if dist/ is missing or stale
EOF

kz-ext dev local   # reads .kz-ext/local.yaml automatically
```

**How it works:**

- **`kz-ext dev local --sdk-repo`**: Mounts the SDK repo into containers at runtime and overrides the published packages with local source (copies Python lib files over installed package, npm pack + install for TypeScript).
- **`kz-ext dev --sdk-repo`**: Bakes the local runtime libraries into Docker images at build time using BuildKit additional build contexts. The resulting images contain your local lib code and are pushed to the cluster normally.
- **`kz-ext doctor`**: Validates the SDK override configuration — checks that the SDK repo exists, Python and TypeScript libs are present, and `dist/` is built.

The override is ephemeral — it never modifies your extension repo's `docker-compose.yml` or Dockerfiles.

### Port Auto-Detection

`kz-ext dev local` automatically detects occupied ports and remaps to the next available:

```
$ kz-ext dev local
Port 3000 in use — remapping frontend to 3001
Port 8000 in use — remapping backend to 8001
frontend: http://localhost:3001
backend:  http://localhost:8001
```

This lets you run multiple extensions simultaneously without port conflicts.

### Multi-Connection Support

```bash
# Add named connections
kz-ext login https://prod.example.com/api --name prod
kz-ext login https://staging.example.com/api --name staging

# List connections
kz-ext login --list

# Switch active connection
kz-ext login --use staging
```

---

The Kamiwaza SDK is actively being developed with new features, examples, and documentation being added regularly. Stay tuned for updates including additional example notebooks, enhanced documentation, and expanded functionality across all services.
