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

## Examples

The `/examples` directory contains Jupyter notebooks demonstrating various use cases:

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
| `kz-ext dev local` | Run the extension locally via Docker Compose with Kamiwaza env vars injected. Auto-detects port conflicts and remaps to available ports. Supports `--sdk-repo`, `--detach`. |
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
