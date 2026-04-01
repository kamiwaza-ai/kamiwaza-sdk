# Extension CLI Foundation + Local Dev (Phase 1) — System Design Working Document

**Status:** Draft
**Version:** 0.1.0
**Date:** 2026-03-31 (updated)
**Projects:** Extension Developer Experience — Phase 1: CLI Foundation + Local Dev
**Authors:** Jonathan

---

## 1. Introduction

This design covers Phase 1 of the Extension Developer Experience initiative: the `kamiwaza-extensions` CLI package (`kz-ext`) and its foundation commands for local extension development. The goal is to give extension developers a single `pip install` that provides authentication, local development, validation, diagnostics, and scaffolding — eliminating the current dependency on Makefile-based tooling and manual environment setup.

Today, developing a Kamiwaza extension requires version bumps, whole-registry rebuilds, and a manual redeploy cycle just to test a code change. The tooling assumes a catalog publishing workflow even when a developer is iterating on a single extension. Phase 1 addresses the first half of this problem: getting developers from zero to a working local environment with one tool and a few commands.

Phase 1 delivers six commands (`login`, `dev local`, `validate`, `doctor`, `create`, and the CLI skeleton itself) as a new `kamiwaza-extensions` PyPI package living in the `kamiwaza-sdk` repo. It depends on `kamiwaza-sdk` for authentication and API access but maintains separate versioning. Phases 2-4 (dev inner loop, seamless redeploy, publishing) build on this foundation but are out of scope here.

### Source Projects (Linear)

| # | Project | Est. | Wave |
|---|---------|------|------|
| 1 | [Extension Developer Experience](https://linear.app/kamiwaza/project/extension-developer-experience-d7a40baac83b) | ~4 phases | 1 |

### Related Projects

| Project | Relevance |
|---------|-----------|
| [v1.0 Commercial Release](https://linear.app/kamiwaza/initiative/v10-commercial-release-3d57e50e707a/projects) | Parent initiative — Phase 1 is prerequisite for extension ecosystem |
| kamiwaza-extensions-template | Current extension tooling being replaced — scaffolding templates, validation scripts, Makefile system |
| kamiwaza-extensions-lib (PyPI v0.1.0) | Python runtime library used in scaffolded extensions — auth, identity, model client |
| @kamiwaza-ai/extensions-lib (npm v0.2.0) | TypeScript runtime library used in scaffolded extensions — SessionProvider, AuthGuard |

---

## 2. Open Questions & Assumptions

| # | Item | Source | Status |
|---|------|--------|--------|
| 1 | CLI framework choice: click vs typer | ENG-3057 | **Resolved: Typer.** Already a transitive dependency via huggingface_hub. Type-hint-driven, built-in prompt/password support, Rich integration for styled output. |
| 2 | Schema for `~/.kamiwaza/config` — how do connection configs and tokens coexist with existing `token.json`? | ENG-3061 / existing codebase | **Resolved: Extend existing mechanism.** Multi-connection config wraps the existing FileTokenStore pattern. Each named connection stores URL + PAT. See Section 4 design. |
| 3 | Reuse existing FileTokenStore or new format? | ENG-3061 / authentication.py | **Resolved: Reuse and extend.** Build on existing `~/.kamiwaza/` directory and token storage patterns. |
| 4 | Env vars for `kz-ext dev local` | ENG-3060 / Developer Guide | **Resolved: Start with documented set.** `KAMIWAZA_API_URL`, `KAMIWAZA_PUBLIC_API_URL`, `KAMIWAZA_ENDPOINT`, `KAMIWAZA_USE_AUTH=false`, `KAMIWAZA_APP_NAME`. Expand if needed during implementation. |
| 5 | Canonical `kamiwaza.json` schema source of truth | ENG-3058 | **Resolved: Adopt from extensions-template.** Schema defined implicitly in `validate-metadata.py`. Port validation logic into the `kamiwaza-extensions` package. |
| 6 | Scaffolding templates: bundled vs remote | ENG-3071 | **Resolved: Bundled.** Templates ship inside the `kamiwaza-extensions` package. The extensions-template repo will be deprecated. |
| 7 | Multiple frontend stacks or one per type? | ENG-3071 / Implementation Plan | **Resolved: One opinionated default per type.** App=Next.js+FastAPI, Tool=FastMCP, Service=minimal. Future phases may add alternatives. |
| 8 | `kamiwaza_version` constraint format | ENG-3058 / Developer Guide | **Resolved: Semver range constraints.** Same format as `kz_ext_version`: operators `>=`, `<=`, `>`, `<`, `==`, `~=`, `!=` with comma-separated compounds (e.g., `">=0.8.0,<1.0.0"`). Defined in extensions-template `validate-metadata.py`. |
| 9 | Legacy `apps/my-app/` nested directory structure support | ENG-3060 / Dev Loop Proposal | **Resolved: Not supported.** Assume flat single-extension repos only. Legacy repos will be migrated separately. |
| 10 | Minimum `kamiwaza-sdk` version | ENG-3057 | **Resolved: `>=0.11.0`** (current release). |
| 11 | Doctor checks for runtime libraries | ENG-3059 / Implementation Plan | **Resolved: Yes.** Doctor will check for `kamiwaza-extensions-lib` in `requirements.txt` and `@kamiwaza-ai/extensions-lib` in `package.json` when present. |
| 12 | Runtime lib version pinning in scaffolded templates | ENG-3071 | **Resolved: Compatible ranges.** `kamiwaza-extensions-lib>=0.1.0` in requirements.txt, `@kamiwaza-ai/extensions-lib: "^0.2.0"` in package.json. Allows patch updates without breaking. |

---

## 3. Existing Foundation

### 3.1 Two-Repo Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Developer Machine                                               │
│                                                                  │
│  ┌──────────────────────────┐   ┌────────────────────────────┐  │
│  │ kamiwaza-sdk (PyPI)      │   │ kamiwaza-extensions-       │  │
│  │                          │   │ template (Copier)          │  │
│  │  KamiwazaClient          │   │                            │  │
│  │   ├─ AuthenticationMgr   │   │  Makefile tooling          │  │
│  │   ├─ ExtensionService    │   │  validate-metadata.py      │  │
│  │   ├─ ServingService      │   │  validate-compose.py       │  │
│  │   └─ ...17 more services │   │  build-registry.py         │  │
│  │                          │   │  manage-templates.py       │  │
│  │  Token: ~/.kamiwaza/     │   │  .env config               │  │
│  │         token.json       │   │  apps/ services/ tools/    │  │
│  └────────────┬─────────────┘   └────────────────────────────┘  │
│               │                          (being deprecated)      │
│               │ HTTPS (requests.Session)                         │
│               ▼                                                  │
│  ┌──────────────────────────┐                                   │
│  │ Kamiwaza Platform API    │                                   │
│  │  /extensions (CRUD)      │                                   │
│  │  /auth (login, PAT)      │                                   │
│  │  /models, /serving, ...  │                                   │
│  └──────────────────────────┘                                   │
└─────────────────────────────────────────────────────────────────┘
```

| Component | Owns | Integration | Extension CLI Awareness |
|-----------|------|-------------|------------------------|
| **kamiwaza-sdk** | API client, auth, token storage | PyPI package, requests.Session | Has ExtensionService for CRD CRUD; no CLI for extensions |
| **kamiwaza-extensions-template** | Makefile tooling, validation scripts, scaffolding, registry builds | Copier template, .env-driven | Full extension lifecycle but via Make targets, not CLI |
| **Kamiwaza Platform** | Extension CRD reconciliation, auth, model serving | REST API, K8s operator | Accepts POST/GET/DELETE /extensions; no PATCH yet |

### 3.2 SDK Client Architecture (`kamiwaza_sdk/client.py`, 427 lines)

**Lazy-Loading Service Pattern:**
```python
class KamiwazaClient:
    def __init__(self, base_url=None, api_key=None, authenticator=None):
        self.session = requests.Session()
        # Auth resolved from: authenticator param > api_key param > env vars

    @property
    def extensions(self):
        if not hasattr(self, "_extensions"):
            from .services.extensions import ExtensionService
            self._extensions = ExtensionService(self)
        return self._extensions
```

**HTTP Methods:** `_request(method, endpoint, *, expect_json=True, skip_auth=False, **kwargs)` + convenience wrappers `get/post/put/delete/patch`

**Auth Flow in _request():**
1. `authenticator.authenticate(session)` — sets Authorization header
2. On 401 → `authenticator.refresh_token(session)` → retry once
3. Bearer token carried in both `Authorization` header and `access_token` cookie

**Environment Variables:**
| Variable | Purpose | Default |
|----------|---------|---------|
| `KAMIWAZA_BASE_URL` | API endpoint | `http://localhost:7777` |
| `KAMIWAZA_API_KEY` / `KAMIWAZA_API_TOKEN` | PAT auth | None |
| `KAMIWAZA_VERIFY_SSL` | SSL verification | `true` |
| `KAMIWAZA_TOKEN_PATH` | Token cache file | `~/.kamiwaza/token.json` |

### 3.3 Authentication System (`kamiwaza_sdk/authentication.py`, 168 lines)

**Authenticator Hierarchy:**
```
Authenticator (ABC)
├── ApiKeyAuthenticator        — Sets Authorization: Bearer {api_key}
├── UserPasswordAuthenticator  — Password grant + token refresh + FileTokenStore
└── OAuthAuthenticator         — Placeholder (NotImplementedError)
```

**UserPasswordAuthenticator Token Lifecycle:**
1. `_load_cached_token()` on init — reads `~/.kamiwaza/token.json` if exists
2. `authenticate(session)` — checks expiry (30s leeway), refreshes if stale
3. `refresh_token(session)` — tries refresh_token grant first, falls back to password grant
4. `_store_token_response(response)` — saves to FileTokenStore (atomic write)

### 3.4 Token Storage (`kamiwaza_sdk/token_store.py`, 74 lines)

**StoredToken Format (`~/.kamiwaza/token.json`):**
```json
{
    "access_token": "eyJ...",
    "refresh_token": "eyJ...",
    "expires_at": 1704067200.0
}
```

**FileTokenStore Pattern:**
- Default path: `Path.home() / ".kamiwaza" / "token.json"`
- Atomic write: writes `.tmp` then `replace()` to final path
- `mkdir(parents=True, exist_ok=True)` on save
- Returns `None` on missing/corrupt file (no crashes)
- **No file permission management** (no chmod 600)

### 3.5 Existing CLI (`kamiwaza_sdk/cli.py`, 202 lines)

**Current Commands (argparse-based):**
| Command | Arguments | Purpose |
|---------|-----------|---------|
| `kamiwaza login` | `--username`, `--password` | Password auth + token cache |
| `kamiwaza pat create` | `--name`, `--ttl`, `--scope`, `--aud`, `--cache-token`, `--revoke-jti` | Create PAT via API |
| `kamiwaza serve deploy` | `--model-id`/`--repo-id` + many options | Deploy model to serving |

**Design Patterns:**
- Dependency injection: `client_factory`, `token_store`, `authenticator_cls` parameters for testability
- Loads cached token via `FileTokenStore` for `pat` and `serve` commands
- All commands construct `KamiwazaClient` internally

### 3.6 Extension Service (`kamiwaza_sdk/services/extensions.py`, 86 lines)

| Method | Endpoint | Returns |
|--------|----------|---------|
| `list_extensions()` | `GET /extensions` | `List[Extension]` |
| `get_extension(name)` | `GET /extensions/{name}` | `Extension` |
| `create_extension(request)` | `POST /extensions` | `Extension` |
| `delete_extension(name)` | `DELETE /extensions/{name}` | `bool` |

**Missing (needed for Phase 2+):** No `PATCH /extensions/{name}` (update) or `GET /extensions/{name}/status` endpoints.

### 3.7 Extension Schemas (`kamiwaza_sdk/schemas/extensions.py`, 130 lines)

**Request Model — `CreateExtension`:**
```python
class CreateExtension(BaseModel):
    name: str                              # K8s DNS label
    type: Literal["app", "tool"]           # Extension type
    version: str                           # Semver
    services: List[ExtensionServiceSpec]   # min_length=1
    kamiwaza: Optional[KamiwazaIntegrationSpec] = None
    networking: Optional[NetworkingSpec] = None
    security: Optional[SecuritySpec] = None
```

**Response Model — `Extension`:**
```python
class Extension(BaseModel):
    name: str
    type: str
    version: str
    phase: Optional[str] = None           # Pending, Running, Failed, etc.
    services: List[ExtensionServiceStatus] = []
    endpoints: Optional[ExtensionEndpoints] = None
    owner_user_id: Optional[str] = None
    created_at: Optional[datetime] = None
```

**Note:** All models use `ConfigDict(extra="allow")` for forward compatibility.

### 3.8 Exception Hierarchy (`kamiwaza_sdk/exceptions.py`, 65 lines)

```
KamiwazaError (base)
├── APIError(message, status_code, response_text, response_data)
│   └── VectorDBUnavailableError
├── AuthenticationError
├── AuthorizationError
├── NotFoundError
│   └── DatasetNotFoundError
├── ValidationError
├── TimeoutError
├── TransportNotSupportedError
└── NonAPIResponseError
```

### 3.9 Extensions-Template Validation Logic

**`validate-metadata.py` — kamiwaza.json Schema (implicit):**

| Field | Type | Required | Validation |
|-------|------|----------|------------|
| `name` | string | Yes | Must match directory name; services must start with `service-`; tools with `tool-`/`mcp-` |
| `version` | string | Yes | Semver: `^\d+\.\d+\.\d+(-[0-9A-Za-z-]+)?(\+[0-9A-Za-z-]+)?$` |
| `source_type` | string | Yes | One of: `kamiwaza`, `public`, `user_repo` |
| `visibility` | string | Yes | One of: `public`, `private`, `team` |
| `description` | string | Yes | Non-empty |
| `risk_tier` | integer | Yes | 0, 1, or 2 |
| `verified` | boolean | Yes | true/false |
| `tags` | list[string] | No | Must be list of strings |
| `env_defaults` | dict | No | Key-value pairs; supports template vars `{app_port}`, `{model_port}`, `{deployment_id}`, `{app_name}` |
| `required_env_vars` | list[string] | No | — |
| `preview_image` | string | No | Must start with `images/`; valid image extension; file must exist |
| `kamiwaza_version` | string | No | Semver range: `>=0.8.0`, `<1.0.0`, `>=0.8.0,<1.0.0` |
| `kz_ext_version` | string | No | **Planned** (not yet validated in template scripts) |
| `category` | string | No | — |
| `template_type` | string | No | Must match extension type |
| `strip_path_prefix` | boolean | No | — |
| `extra_docker_images` | list | No | Additional images to export |
| `preferred_model_type` | string | No | — |
| `image` | string | No | Tools only; Docker image reference |
| `capabilities` | list[string] | No | Tools only |

**`validate-compose.py` — Deployment Compatibility Rules:**

| Rule | Severity | What's Checked |
|------|----------|---------------|
| No host port bindings | Error | `"8080:3000"` format not allowed; container-only ports required |
| Named volumes only | Error | No bind mounts (`./`, `../`, absolute paths) |
| No build section | Error | Pre-built images only |
| Resource limits required | Error | `deploy.resources.limits` must be present |
| No container_name | Error | Platform manages naming |
| No custom networks | Error | Platform manages networking |
| Extra hosts required | Error | If using `host.docker.internal`, must have `host-gateway` mapping |
| Reserved volume prefixes | Error | `kamiwaza-*`, `buildx_buildkit_*` forbidden |
| Image naming | Warning | Must contain extension identifier in `kamiwazaai/` prefixed images |

### 3.10 Platform Environment Variables (Runtime Contract)

| Variable | Injected By | Purpose |
|----------|-------------|---------|
| `KAMIWAZA_DEPLOYMENT_ID` | Platform | Unique deployment identifier |
| `KAMIWAZA_APP_NAME` | Platform | Extension name from kamiwaza.json |
| `KAMIWAZA_APP_PORT` | Platform | Primary service port |
| `KAMIWAZA_APP_PATH` | Platform | Ingress path prefix |
| `KAMIWAZA_APP_URL` | Platform | Full public URL |
| `KAMIWAZA_API_URL` | Platform | Internal API endpoint |
| `KAMIWAZA_PUBLIC_API_URL` | Platform | Public API endpoint |
| `KAMIWAZA_ORIGIN` | Platform | CORS origin |
| `KAMIWAZA_USE_AUTH` | Platform | `"true"`/`"false"` |
| `KAMIWAZA_MODEL_PORT` | Platform | Paired model endpoint port |
| `KAMIWAZA_MODEL_URL` | Platform | Full model URL |
| `KAMIWAZA_ENDPOINT` | Platform | Model endpoint (legacy alias) |

**For `kz-ext dev local`, the CLI must inject a subset:** `KAMIWAZA_API_URL`, `KAMIWAZA_PUBLIC_API_URL`, `KAMIWAZA_ENDPOINT`, `KAMIWAZA_USE_AUTH=false`, `KAMIWAZA_APP_NAME`.

### 3.11 Platform Interfaces

| Interface | Infrastructure | Current Usage | Integration Pattern |
|-----------|---------------|---------------|-------------------|
| **Authentication** | SDK Authenticator hierarchy, FileTokenStore, `~/.kamiwaza/token.json` | Login, PAT creation, auto-refresh on 401 | Authenticator strategy injected into KamiwazaClient |
| **Extension CRD API** | REST endpoints on Kamiwaza platform, K8s operator reconciles CRs | CRUD via ExtensionService | POST/GET/DELETE; no PATCH yet |
| **Docker/Compose** | Docker daemon + Compose (v1 or v2 plugin) | Used by extensions-template Makefile for local dev | `docker-compose up --build` with env overlay |
| **Validation** | Python scripts in extensions-template | `make validate` runs metadata + compose checks | Rule-based validation with pass/fail/warn output |
| **Config Storage** | `~/.kamiwaza/token.json` via FileTokenStore | Single connection, single token | JSON file with atomic writes |

### 3.12 Codebase Snapshot

| Repository | Branch | Commit | Date | Relevant Paths |
|-----------|--------|--------|------|---------------|
| `/Users/jonathan/repos/kamiwaza-sdk` | develop | `208d9dd` | 2026-03-26 | `kamiwaza_sdk/client.py`, `authentication.py`, `token_store.py`, `cli.py`, `config.py`, `exceptions.py`, `services/extensions.py`, `schemas/extensions.py`, `pyproject.toml` |
| `/Users/jonathan/repos/kamiwaza-extensions-template` | feature/revamp | `0f03cd8` | 2026-03-20 | `scripts/validate-metadata.py`, `scripts/validate-compose.py`, `make/dev.mk`, `make/metadata.mk`, `.dev-docs/`, `copier.yml` |

### 3.13 Architectural Decision: New Package, Not SDK Extension

**Decision:** `kamiwaza-extensions` is a separate PyPI package in the same repo, not a new service added to `KamiwazaClient`.

**Rationale:**
- The CLI tool serves extension *developers*, not SDK API consumers
- Commands orchestrate Docker, file I/O, and subprocess management — fundamentally different from API client operations
- Independent versioning allows CLI releases without SDK version bumps
- The extensions-template Makefile system it replaces is also separate from the SDK

**What `kamiwaza-extensions` needs from `kamiwaza-sdk`:**
- `KamiwazaClient` for API calls (login, PAT creation, extension CRUD)
- `Authenticator` hierarchy for auth flows
- `FileTokenStore` for token persistence (extended for multi-connection)
- Exception types for error handling

**What `kamiwaza-extensions` does NOT need:**
- To modify any existing SDK code
- To register as a service on KamiwazaClient
- To share schemas with the SDK (it has its own: kamiwaza.json, config file)

### 3.14 Architectural Decision: Extend Config, Don't Replace

**Decision:** Build multi-connection config on top of the existing `~/.kamiwaza/` directory and `FileTokenStore` pattern, rather than replacing it.

**Rationale:**
- SDK users already have `~/.kamiwaza/token.json` from `kamiwaza login`
- The new `~/.kamiwaza/config` file stores connection metadata (URL, name, active flag)
- Each connection's PAT is stored as a separate token file: `~/.kamiwaza/connections/{name}/token.json`
- The default (unnamed) connection maps to the existing `~/.kamiwaza/token.json` for backward compatibility
- SDK commands continue to work unchanged

### 3.15 Responsibility Matrix for kz-ext Phase 1

| Feature | Owns | Why |
|---------|------|-----|
| CLI framework + subcommand routing | `kamiwaza-extensions` package | New package; independent from SDK CLI |
| Connection config management | `kamiwaza-extensions` package | Multi-connection is extension-dev-specific |
| Authentication flow (login) | `kamiwaza-sdk` (reused) | Existing Authenticator + FileTokenStore |
| PAT creation for connections | `kamiwaza-sdk` (reused) | Existing `auth.create_pat()` |
| Docker Compose orchestration | `kamiwaza-extensions` package | New capability; subprocess management |
| Environment variable injection | `kamiwaza-extensions` package | Extension-dev-specific |
| kamiwaza.json validation | `kamiwaza-extensions` package | Ported from extensions-template scripts |
| Compose deployment validation | `kamiwaza-extensions` package | Ported from extensions-template scripts |
| Extension scaffolding templates | `kamiwaza-extensions` package | Bundled templates replacing Copier |
| Doctor diagnostics | `kamiwaza-extensions` package | New capability |

---

## 4. Detailed Design

### 4.1 UC Traceability Matrix

| Design Component | Covers UCs | Feature Group |
|-----------------|------------|---------------|
| **CLI App** | UAC-1, UAC-2, UAC-3, INF-P16 | Package Setup |
| **ConnectionManager** | UAC-4, UAC-5, UAC-6, UAC-7, UAC-8, UAC-9, INF-P1, INF-P2, INF-P3, INF-P4, INF-P5, INF-P17 | Login |
| **DevLocalRunner** | UAC-10, UAC-11, UAC-12, UAC-13, UAC-14, INF-P6, INF-P7, INF-P11, INF-P12, INF-P13 | Dev Local |
| **MetadataValidator** | UAC-15, UAC-18, UAC-29, INF-P10, INF-P14, INF-P18 | Validate |
| **ComposeValidator** | UAC-16, UAC-17, UAC-18, INF-P10 | Validate |
| **DoctorChecker** | UAC-19, UAC-20, UAC-21, UAC-22, UAC-23 | Doctor |
| **Scaffolder** | UAC-24, UAC-25, UAC-26, UAC-27, UAC-28, INF-P8, INF-P9, INF-P15 | Create |
| **Bundled Templates** | UAC-24, UAC-25, UAC-26, UAC-28 | Create |

---

### 4.2 Component Architecture

#### 4.2.1 Component Inventory

| Component | Type | Boundary | Responsibility | Dependencies |
|-----------|------|----------|----------------|-------------|
| CLI App | module | Subcommand routing + global flags | Parse args, dispatch to handlers | Typer, all components below |
| ConnectionManager | module | `~/.kamiwaza/` config lifecycle | Multi-connection CRUD, active switching, token resolution | kamiwaza-sdk (FileTokenStore, Authenticator) |
| DevLocalRunner | module | Docker Compose subprocess | Extension detection, env overlay, compose invocation, signal forwarding | ConnectionManager, subprocess |
| MetadataValidator | module | kamiwaza.json validation rules | Schema validation, field checks, version format, naming conventions | None (pure logic) |
| ComposeValidator | module | docker-compose.yml validation rules | Deployment compatibility checks (ports, volumes, resources) | None (pure logic) |
| DoctorChecker | module | Environment diagnostics | Check Docker, Python, CLI version, connection, runtime libs | ConnectionManager, subprocess |
| Scaffolder | module | Template rendering + directory creation | Read bundled templates, substitute variables, write files, git init | Bundled Templates |
| Bundled Templates | package data | Static template files | Provide app/tool/service scaffolding content | None |
| kamiwaza-sdk | external (library) | Existing PyPI package | HTTP client, auth, token store, extension CRD API | Kamiwaza Platform API |

#### 4.2.2 Component Dependency Diagram

```
                      ┌─────────────────────────────┐
                      │         CLI App              │
                      │   (Typer entry point)        │
                      └──────────┬──────────────────-┘
                                 │
          ┌──────────┬───────────┼───────────┬──────────┬──────────┐
          ▼          ▼           ▼           ▼          ▼          ▼
   ┌────────────┐ ┌────────┐ ┌──────────┐ ┌────────┐ ┌────────┐ ┌────────┐
   │ Connection │ │DevLocal│ │ Metadata │ │Compose │ │Doctor  │ │Scaffold│
   │ Manager    │ │Runner  │ │ Validator│ │Validatr│ │Checker │ │er      │
   └──────┬─────┘ └───┬────┘ └──────────┘ └────────┘ └───┬────┘ └───┬────┘
          │            │                                   │          │
          │            │                                   │     ┌────▼────┐
          │            ├───────────────────────────────────┘     │Bundled  │
          │            │                                         │Templates│
          │            │                                         └─────────┘
          ▼            ▼
   ┌─────────────────────────┐          ┌──────────────────┐
   │     kamiwaza-sdk        │          │  Docker Compose   │
   │  (KamiwazaClient,      │          │  (subprocess)     │
   │   Authenticator,        │          └──────────────────┘
   │   FileTokenStore)       │
   └────────────┬────────────┘
                │
                ▼
   ┌──────────────────────────┐
   │  Kamiwaza Platform API   │
   └──────────────────────────┘
```

#### 4.2.3 CLI App

**Type:** module
**Boundary:** Subcommand routing, global flags, error formatting. Does NOT contain business logic.
**Location:** `kamiwaza_extensions/cli.py`
**Dependencies:** Typer, Rich (for styled output), all handler modules

```python
import typer

app = typer.Typer(
    name="kz-ext",
    help="Kamiwaza extension developer tools",
    no_args_is_help=True,
)

# Global options
@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    debug: bool = typer.Option(False, "--debug", help="Show debug output including tracebacks"),
):
    """Kamiwaza extension developer tools."""
    # Store in typer context for child commands
    ...

# Subcommands registered via app.command() or app.add_typer()
# login, dev (with "local" subcommand), validate, doctor, create
```

**Error handling wrapper:**
All commands wrapped to catch exceptions and format consistently:
- `KamiwazaError` → styled error message + suggested fix
- `FileNotFoundError` → "File not found: {path}"
- Unhandled → "Internal error" (traceback only with `--debug`)

*Traces to: UAC-1, UAC-2, UAC-3, INF-P16*

#### 4.2.4 ConnectionManager

**Type:** module
**Boundary:** Config file I/O, connection CRUD, token resolution. Does NOT perform authentication (delegates to SDK).
**Location:** `kamiwaza_extensions/connections.py`
**Dependencies:** kamiwaza-sdk (`FileTokenStore`, `StoredToken`)

**Config file:** `~/.kamiwaza/config` (JSON, version 1)
```json
{
    "version": 1,
    "active_connection": "default",
    "connections": {
        "default": { "url": "https://cluster.example/api", "created_at": 1711900800.0 },
        "staging": { "url": "https://staging.example/api", "created_at": 1711900900.0 }
    }
}
```

**Token files:** `~/.kamiwaza/connections/{name}/token.json` (same `StoredToken` format as SDK)

**API:**
```python
class ConnectionManager:
    def __init__(self, config_dir: Path = None):
        """Default: ~/.kamiwaza/"""

    def add_connection(self, name: str, url: str, token: StoredToken) -> None:
        """Store a new connection with its token. Sets as active if first connection."""

    def remove_connection(self, name: str) -> None:
        """Remove connection and its token file."""

    def list_connections(self) -> List[ConnectionInfo]:
        """Return all connections with active flag."""

    def get_active_connection(self) -> Optional[ConnectionInfo]:
        """Return the currently active connection, or None."""

    def set_active(self, name: str) -> None:
        """Switch active connection."""

    def get_token(self, name: str = None) -> Optional[StoredToken]:
        """Load token for named connection (default: active). Returns None if missing/expired."""

    def save_token(self, token: StoredToken, name: str = None) -> None:
        """Save token for named connection (default: active)."""
```

**File operations:**
- `mkdir(parents=True, exist_ok=True)` + `chmod 700` on `~/.kamiwaza/`
- Atomic writes (`.tmp` + `replace()`) following existing `FileTokenStore` pattern
- `chmod 600` on config and token files
- Returns `None` / raises clear errors on corrupt/missing files

*Traces to: UAC-4, UAC-5, UAC-6, UAC-7, UAC-8, UAC-9, INF-P1, INF-P2, INF-P3, INF-P4, INF-P5, INF-P17*

#### 4.2.5 DevLocalRunner

**Type:** module
**Boundary:** Extension detection, env construction, subprocess lifecycle. Does NOT validate extension structure (that's MetadataValidator).
**Location:** `kamiwaza_extensions/dev_local.py`
**Dependencies:** ConnectionManager, subprocess, signal

**Extension detection logic:**
1. Check `./kamiwaza.json` — if found, use current directory
2. Check `*/kamiwaza.json` one level deep — if exactly one found, use that directory
3. If zero found → error: "No kamiwaza.json found. Run this in an extension directory or use `kz-ext create`."
4. If multiple found at one level → error: "Multiple kamiwaza.json found. Run from inside a specific extension directory."

**Environment variable overlay:**
```python
def build_env_overlay(connection: ConnectionInfo, extension_name: str) -> Dict[str, str]:
    return {
        "KAMIWAZA_API_URL": connection.url,
        "KAMIWAZA_PUBLIC_API_URL": connection.url.replace("/api", ""),
        "KAMIWAZA_ENDPOINT": f"{connection.url}/v1",
        "KAMIWAZA_USE_AUTH": "false",
        "KAMIWAZA_APP_NAME": extension_name,
    }
```

**Docker Compose detection:**
```python
def detect_compose_command() -> List[str]:
    # Try: docker compose version (v2 plugin)
    # Fallback: docker-compose --version (v1 standalone)
    # Error: "Docker Compose not found. Install Docker Desktop or docker-compose."
```

**Compose file detection:** Checks in order: `docker-compose.yml`, `docker-compose.yaml`, `compose.yml`, `compose.yaml`.

**Subprocess management:**
- `subprocess.Popen(compose_cmd + ["up", "--build"], env=merged_env, ...)` with stdout/stderr piped through
- Register `signal.signal(SIGINT, handler)` and `signal.signal(SIGTERM, handler)` that forward to subprocess
- Wait for subprocess exit and return its exit code

*Traces to: UAC-10, UAC-11, UAC-12, UAC-13, UAC-14, INF-P6, INF-P7, INF-P11, INF-P12, INF-P13*

#### 4.2.6 MetadataValidator

**Type:** module
**Boundary:** kamiwaza.json validation rules only. Does NOT validate compose files or check external systems.
**Location:** `kamiwaza_extensions/validators/metadata.py`
**Dependencies:** None (pure logic, Pydantic for schema)

**Pydantic schema (source of truth):**
```python
class KamiwazaMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(..., min_length=1)
    version: str  # validated by @field_validator
    source_type: Literal["kamiwaza", "public", "user_repo"]
    visibility: Literal["public", "private", "team"]
    description: str = Field(..., min_length=1)
    risk_tier: Literal[0, 1, 2]
    verified: bool

    # Optional
    tags: Optional[List[str]] = None
    env_defaults: Optional[Dict[str, str]] = None
    required_env_vars: Optional[List[str]] = None
    preview_image: Optional[str] = None
    kamiwaza_version: Optional[str] = None  # semver range
    kz_ext_version: Optional[str] = None    # semver range
    category: Optional[str] = None
    preferred_model_type: Optional[str] = None
    strip_path_prefix: Optional[bool] = None
```

**Validation result model:**
```python
@dataclass
class ValidationResult:
    passed: bool
    errors: List[str]    # Fatal issues (exit code 1)
    warnings: List[str]  # Advisory (exit code 0)
```

**Validation rules (ported from validate-metadata.py):**
- Required field presence and types
- Version format: semver regex
- kamiwaza_version / kz_ext_version: semver range regex
- kz_ext_version compatibility check against installed CLI version
- Naming conventions: tool-/mcp- prefix for tools, service- prefix for services
- preview_image: starts with `images/`, valid extension, file exists
- Optional field type checking (tags=list, env_defaults=dict, etc.)

*Traces to: UAC-15, UAC-18, UAC-29, INF-P10, INF-P14, INF-P18*

#### 4.2.7 ComposeValidator

**Type:** module
**Boundary:** docker-compose.yml deployment compatibility rules. Reports warnings for local dev issues, errors for blocking problems.
**Location:** `kamiwaza_extensions/validators/compose.py`
**Dependencies:** None (pure logic, PyYAML for parsing)

**Validation rules (ported from validate-compose.py):**

| Rule | Severity | Check |
|------|----------|-------|
| Host port bindings | Warning | `"8080:3000"` format detected |
| Bind mounts | Warning | `./`, `../`, absolute paths in volumes |
| Missing resource limits | Warning | No `deploy.resources.limits` |
| Explicit container_name | Warning | Platform manages naming |
| Custom networks | Warning | Platform manages networking |
| Build section present | Info | Expected for local dev; will be stripped for deployment |
| Missing extra_hosts | Warning | If `host.docker.internal` in env but no extra_hosts mapping |
| Dockerfiles missing | Error | If `build` references nonexistent Dockerfile |

**Note:** All compose rules report as **warnings** (not errors) since the local compose file is expected to differ from deployment requirements. Only missing Dockerfiles are errors.

*Traces to: UAC-16, UAC-17, UAC-18, INF-P10*

#### 4.2.8 DoctorChecker

**Type:** module
**Boundary:** Environment diagnostics. Aggregates check results. Does NOT fix problems.
**Location:** `kamiwaza_extensions/doctor.py`
**Dependencies:** ConnectionManager, subprocess

**Checks:**
```python
@dataclass
class CheckResult:
    name: str
    status: Literal["pass", "fail", "warn"]
    message: str
    fix: Optional[str] = None  # Suggested fix for fail/warn

checks = [
    # System checks (always run)
    ("Python version", check_python_version),          # >= 3.10
    ("Docker installed", check_docker_installed),       # docker info
    ("Docker Compose available", check_compose),        # docker compose version
    ("Docker running", check_docker_running),           # docker info succeeds

    # Connection checks (if configured)
    ("Kamiwaza connection", check_connection),           # GET /health on active connection

    # Extension checks (if in extension directory)
    ("CLI version compatibility", check_cli_version),    # kz_ext_version range
    ("Runtime lib (Python)", check_python_runtime_lib),  # kamiwaza-extensions-lib in requirements.txt
    ("Runtime lib (TypeScript)", check_ts_runtime_lib),  # @kamiwaza-ai/extensions-lib in package.json
]
```

*Traces to: UAC-19, UAC-20, UAC-21, UAC-22, UAC-23*

#### 4.2.9 Scaffolder

**Type:** module
**Boundary:** Template rendering and directory creation. Does NOT validate output (user runs `kz-ext validate` after).
**Location:** `kamiwaza_extensions/scaffolder.py`
**Dependencies:** Bundled Templates (package data), subprocess (for `git init`)

**Template variable context:**
```python
context = {
    "name": name,                          # e.g., "my-app"
    "version": "0.1.0",                    # initial version
    "kz_ext_version": f">={cli_version},<{next_major}",  # compatible range
    "python_runtime_lib_version": ">=0.1.0",
    "ts_runtime_lib_version": "^0.2.0",
    "description": f"A Kamiwaza {type} extension",
}
```

**Directory structures by type:**

**App (Next.js + FastAPI):**
```
{name}/
├── kamiwaza.json
├── docker-compose.yml
├── README.md
├── .gitignore
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   ├── next.config.js
│   └── src/
│       ├── app/
│       │   ├── layout.tsx     # SessionProvider + AuthGuard
│       │   └── page.tsx
│       └── lib/
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt       # kamiwaza-extensions-lib>=0.1.0
│   └── app/
│       └── main.py            # FastAPI + create_session_router() + /health
└── images/
```

**Tool (FastMCP):**
```
{name}/
├── kamiwaza.json
├── docker-compose.yml
├── Dockerfile
├── requirements.txt           # kamiwaza-extensions-lib>=0.1.0, fastmcp
├── README.md
├── .gitignore
└── src/
    └── server.py              # FastMCP server with example tool
```

**Service (minimal):**
```
{name}/
├── kamiwaza.json
├── docker-compose.yml
├── Dockerfile
├── README.md
└── .gitignore
```

**Pre-flight checks:**
1. Target directory does not exist → error if it does (INF-P9)
2. Name validation: lowercase, alphanumeric + hyphens, convention prefix applied if needed (INF-P8)

**Post-creation:**
1. `git init` in created directory (warn if git unavailable)
2. Print success message with next steps

*Traces to: UAC-24, UAC-25, UAC-26, UAC-27, UAC-28, INF-P8, INF-P9, INF-P15*

#### 4.2.10 Bundled Templates

**Type:** package data
**Boundary:** Static files only. No logic.
**Location:** `kamiwaza_extensions/templates/{app,tool,service}/`
**Dependencies:** None

Templates use simple string substitution (`{name}`, `{version}`, etc.) — no Jinja2 or complex templating engine. This keeps the dependency footprint minimal and templates easy to read.

Files are included via `pyproject.toml`:
```toml
[tool.setuptools.package-data]
kamiwaza_extensions = ["templates/**/*"]
```

*Traces to: UAC-24, UAC-25, UAC-26, UAC-28*

---

### 4.3 Layer View

#### 4.3.1 Layer Mapping

| Layer | Components | Key Responsibilities |
|-------|-----------|---------------------|
| **CLI / Presentation** | CLI App | Argument parsing, subcommand dispatch, styled output, error formatting |
| **Business Logic** | ConnectionManager, DevLocalRunner, MetadataValidator, ComposeValidator, DoctorChecker, Scaffolder | Core logic per feature area |
| **Data / Config** | ConnectionManager (config I/O), Bundled Templates | Persistent config in `~/.kamiwaza/`, static template files |
| **External / Infrastructure** | kamiwaza-sdk, Docker Compose (subprocess), filesystem, git | API calls, container orchestration, file I/O |

#### 4.3.2 CLI / Presentation Layer — Design Notes

**Conventions:**
- All output via Typer/Rich (not raw `print()`)
- Errors: `typer.echo(err, err=True)` to stderr
- Exit codes: 0 = success, 1 = error
- `--verbose` flag increases output detail
- `--debug` flag shows tracebacks
- `--json` flag on `validate` for machine-readable output

**New in this design:** Typer replaces argparse. The new `kz-ext` entry point is completely separate from the existing `kamiwaza` CLI in the SDK.

#### 4.3.3 Business Logic Layer — Design Notes

**Conventions:**
- Each module is independently testable (no cross-module dependencies except ConnectionManager)
- All modules accept config as parameters (no global state)
- Validators return `ValidationResult` dataclass (not raise exceptions)
- Doctor returns `List[CheckResult]`

**New in this design:** All modules are new. Logic ported from extensions-template validation scripts is rewritten as clean Python classes with Pydantic models, not 1:1 script ports.

---

### 4.4 Systemic / Platform Interfaces

#### 4.4.1 Interface Integration Summary

| Interface | Current State (Section 3) | Design Changes | Priority |
|-----------|--------------------------|---------------|----------|
| Authentication | SDK Authenticator hierarchy + FileTokenStore | Extended: multi-connection config, per-connection token files | P1 |
| Config Storage | `~/.kamiwaza/token.json` (single file) | Extended: `~/.kamiwaza/config` + `connections/{name}/token.json` | P1 |
| Docker Integration | Makefile `make dev` targets | New: programmatic Compose invocation with env overlay | P1 |
| Validation | Python scripts in extensions-template | Replaced: Pydantic-based validation in kz-ext package | P1 |
| Extension CRD API | CRUD via ExtensionService | No change in Phase 1 (used by Phase 2+) | — |

#### 4.4.2 Authentication

**Current state:** See Section 3.3. SDK provides `ApiKeyAuthenticator` and `UserPasswordAuthenticator` with `FileTokenStore`.

**Design changes:**
- `kz-ext login` with username/password creates a PAT via `client.auth.create_pat()` and stores it as a long-lived token for the connection
- `kz-ext login --api-key` stores the provided key directly
- Each connection gets its own token file under `~/.kamiwaza/connections/{name}/token.json`
- The `ConnectionManager` wraps `FileTokenStore` for per-connection token management
- SDK's existing `ApiKeyAuthenticator` is used when commands need API access (initialized with the stored PAT)

**Failure mode:** If the stored PAT expires or is revoked, commands that need API access fail with "Connection expired. Run `kz-ext login <url>` to re-authenticate."

#### 4.4.3 Config Storage

**Current state:** See Section 3.4. Single `~/.kamiwaza/token.json`.

**Design changes:**
```
~/.kamiwaza/                          # 700 permissions
├── token.json                        # Existing SDK token (unchanged)
├── config                            # NEW: multi-connection metadata (600)
└── connections/                      # NEW: per-connection tokens
    ├── default/
    │   └── token.json                # 600 permissions
    └── staging/
        └── token.json                # 600 permissions
```

**Backward compatibility:** The existing `~/.kamiwaza/token.json` is not touched. SDK commands continue to work. If a user has an existing token.json and runs `kz-ext login` for the first time, the CLI creates the new config structure alongside it.

**Failure mode:** Corrupt config file → clear error message: "Config file is corrupted. Run `kz-ext login <url>` to create a new connection."

---

### 4.5 Key Interaction Sequences

#### Sequence 1: kz-ext login (Happy Path)

```
Developer                CLI App              ConnectionMgr         KamiwazaClient (SDK)      Platform API
  │                        │                      │                       │                      │
  ├─ kz-ext login <url> ──►│                      │                       │                      │
  │                        ├─ prompt password ────►│                       │                      │
  │◄── enter password ─────┤                      │                       │                      │
  │                        ├─ create client ───────┼──────────────────────►│                      │
  │                        │                      │                       ├─ POST /auth/login ───►│
  │                        │                      │                       │◄── token ─────────────┤
  │                        │                      │                       ├─ POST /auth/pat ─────►│
  │                        │                      │                       │◄── PAT ───────────────┤
  │                        ├─ save connection ────►│                       │                      │
  │                        │                      ├─ write config          │                      │
  │                        │                      ├─ write token.json      │                      │
  │                        │                      ├─ chmod 600             │                      │
  │◄── "Connected to ..." ─┤                      │                       │                      │
```

#### Sequence 2: kz-ext dev local (Happy Path)

```
Developer              CLI App           DevLocalRunner        ConnectionMgr       Docker Compose
  │                      │                    │                    │                    │
  ├─ kz-ext dev local ──►│                    │                    │                    │
  │                      ├─ run() ───────────►│                    │                    │
  │                      │                    ├─ find kamiwaza.json│                    │
  │                      │                    ├─ get connection ───►│                    │
  │                      │                    │◄── connection info ─┤                    │
  │                      │                    ├─ build env overlay  │                    │
  │                      │                    ├─ detect compose cmd │                    │
  │                      │                    ├─ find compose file  │                    │
  │                      │                    ├─ Popen(up --build) ─────────────────────►│
  │◄── container logs ───┼────────────────────┼── stdout passthru ◄─────────────────────┤
  │                      │                    │                    │                    │
  │── Ctrl+C ───────────►│                    │                    │                    │
  │                      ├─ SIGINT ──────────►│                    │                    │
  │                      │                    ├─ forward SIGINT ───────────────────────►│
  │                      │                    │◄── exit code ──────────────────────────┤
  │◄── exit ─────────────┤                    │                    │                    │
```

#### Sequence 3: kz-ext validate (Errors Found)

```
Developer              CLI App           MetadataValidator      ComposeValidator
  │                      │                    │                      │
  ├─ kz-ext validate ───►│                    │                      │
  │                      ├─ validate() ──────►│                      │
  │                      │                    ├─ load kamiwaza.json   │
  │                      │                    ├─ check required fields│
  │                      │                    ├─ check version format │
  │                      │                    ├─ check naming rules   │
  │                      │                    ├─ check kz_ext_version │
  │                      │◄── result(errors, warnings)               │
  │                      │                    │                      │
  │                      ├─ validate() ──────┼─────────────────────►│
  │                      │                    │                      ├─ load compose file
  │                      │                    │                      ├─ check ports
  │                      │                    │                      ├─ check volumes
  │                      │                    │                      ├─ check resources
  │                      │◄── result(warnings)┼──────────────────────┤
  │                      │                    │                      │
  │◄── "2 errors,        │                    │                      │
  │     3 warnings"      │                    │                      │
  │     exit code 1      │                    │                      │
```

#### Sequence 4: kz-ext create --type app

```
Developer              CLI App           Scaffolder             Filesystem
  │                      │                    │                    │
  ├─ kz-ext create       │                    │                    │
  │  --type app          │                    │                    │
  │  --name my-app ─────►│                    │                    │
  │                      ├─ create() ────────►│                    │
  │                      │                    ├─ validate name      │
  │                      │                    ├─ check dir exists ──►│
  │                      │                    │◄── not found ───────┤
  │                      │                    ├─ load app templates  │
  │                      │                    ├─ substitute vars     │
  │                      │                    ├─ write files ───────►│
  │                      │                    │  ├─ my-app/kamiwaza.json
  │                      │                    │  ├─ my-app/docker-compose.yml
  │                      │                    │  ├─ my-app/frontend/...
  │                      │                    │  ├─ my-app/backend/...
  │                      │                    │  └─ my-app/.gitignore
  │                      │                    ├─ git init ──────────►│
  │◄── "Created my-app/" ┤                    │                    │
  │    "Next: cd my-app"  │                    │                    │
  │    "     kz-ext dev"  │                    │                    │
```

---

### 4.6 Data Model Changes (Consolidated)

#### Filesystem (`~/.kamiwaza/`)

| Path | Change | Detail |
|------|--------|--------|
| `~/.kamiwaza/` | **No change** | Directory already exists from SDK |
| `~/.kamiwaza/token.json` | **No change** | Existing SDK token untouched |
| `~/.kamiwaza/config` | **New file** | Multi-connection metadata (JSON, version 1) |
| `~/.kamiwaza/connections/{name}/token.json` | **New files** | Per-connection PAT storage |

#### kamiwaza.json Schema

| Field | Change | Detail |
|-------|--------|--------|
| `kz_ext_version` | **New field** (optional) | Semver range for CLI compatibility (e.g., `">=1.0.0,<2.0.0"`) |
| All existing fields | **No change** | Validation rules ported from extensions-template |

#### PyPI Package

| Artifact | Change | Detail |
|----------|--------|--------|
| `kamiwaza-extensions` | **New package** | Separate from `kamiwaza-sdk`, independent versioning |

---

### 4.7 UX Mocks

#### kz-ext --help

```
$ kz-ext --help

 Usage: kz-ext [OPTIONS] COMMAND [ARGS]...

 Kamiwaza extension developer tools

 Options:
   -v, --verbose  Verbose output
   --debug        Show debug output including tracebacks
   --version      Show version and exit
   --help         Show this message and exit

 Commands:
   login     Connect to a Kamiwaza instance
   dev       Development commands
   validate  Validate extension metadata and compose file
   doctor    Check development environment
   create    Scaffold a new extension
```

#### kz-ext login

```
$ kz-ext login https://my-cluster.kamiwaza.test/api
Username: admin
Password: ********
  Authenticating... done
  Creating access token... done
  Saving connection "default"... done

Connected to https://my-cluster.kamiwaza.test/api as admin

$ kz-ext login https://staging.kamiwaza.test/api --name staging
Username: admin
Password: ********
  Authenticating... done
  Creating access token... done
  Saving connection "staging"... done

Connected to https://staging.kamiwaza.test/api as admin

$ kz-ext login --list
  NAME       URL                                     ACTIVE
  default    https://my-cluster.kamiwaza.test/api     *
  staging    https://staging.kamiwaza.test/api

$ kz-ext login --use staging
Switched to connection "staging"
```

#### kz-ext login (Error Cases)

```
$ kz-ext login https://bad-url.example/api
Username: admin
Password: ********
Error: Could not connect to https://bad-url.example/api
  Check the URL and try again. Use kz-ext doctor to diagnose connection issues.

$ kz-ext login https://cluster.test/api
Username: admin
Password: wrong-password
Error: Authentication failed (invalid credentials)
  Check your username and password and try again.
```

#### kz-ext dev local

```
$ kz-ext dev local
  Extension: my-app (v1.0.0)
  Connection: default (https://my-cluster.kamiwaza.test/api)
  Compose: docker compose up --build

[+] Building 12.3s (8/8) FINISHED
 => [backend] ...
 => [frontend] ...
[+] Running 2/2
 - Container my-app-backend-1   Started
 - Container my-app-frontend-1  Started
backend-1   | INFO:     Uvicorn running on http://0.0.0.0:8000
frontend-1  | ready - started server on 0.0.0.0:3000

^C Shutting down...
[+] Stopping 2/2
 - Container my-app-frontend-1  Stopped
 - Container my-app-backend-1   Stopped

$ kz-ext dev local  # No connection configured
  Extension: my-app (v1.0.0)
  Connection: none (running in standalone mode)
  Compose: docker compose up --build
  ...
```

#### kz-ext dev local (Error Cases)

```
$ kz-ext dev local  # Not in extension directory
Error: No kamiwaza.json found
  Run this command from an extension directory, or use kz-ext create to scaffold one.

$ kz-ext dev local  # Docker not running
Error: Docker is not running
  Start Docker Desktop or run: sudo systemctl start docker
```

#### kz-ext validate

```
$ kz-ext validate
  Validating kamiwaza.json... done
  Validating docker-compose.yml... done

  ERRORS (1):
    kamiwaza.json: Missing required field: visibility

  WARNINGS (2):
    kamiwaza.json: Missing optional field: preview_image
    docker-compose.yml: Service "backend" has host port binding (8000:8000)
      This is fine for local dev but will be stripped for deployment.

  Result: FAIL (1 error, 2 warnings)

$ kz-ext validate  # All good
  Validating kamiwaza.json... done
  Validating docker-compose.yml... done

  WARNINGS (1):
    docker-compose.yml: Service "backend" has host port binding (8000:8000)

  Result: PASS (0 errors, 1 warning)

$ kz-ext validate --json
{"passed": true, "errors": [], "warnings": ["docker-compose.yml: ..."]}
```

#### kz-ext doctor

```
$ kz-ext doctor
  Checking development environment...

  PASS  Python version (3.11.5 >= 3.10)
  PASS  Docker installed (Docker 27.1.1)
  PASS  Docker Compose available (v2.29.1)
  PASS  Docker running
  PASS  Kamiwaza connection (https://my-cluster.kamiwaza.test/api)
  PASS  CLI version compatibility (1.0.0 matches >=1.0.0,<2.0.0)
  WARN  Runtime lib (Python): kamiwaza-extensions-lib not in requirements.txt
  PASS  Runtime lib (TypeScript): @kamiwaza-ai/extensions-lib ^0.2.0

  Result: 6 passed, 1 warning, 0 failed

$ kz-ext doctor  # Problems found
  PASS  Python version (3.11.5 >= 3.10)
  FAIL  Docker installed
        Docker not found. Install from https://docs.docker.com/get-docker/
  FAIL  Docker Compose available
        Requires Docker with Compose plugin or standalone docker-compose
  PASS  Kamiwaza connection (https://my-cluster.kamiwaza.test/api)
  WARN  CLI version compatibility: No kz_ext_version in kamiwaza.json

  Result: 2 passed, 1 warning, 2 failed
```

#### kz-ext create

```
$ kz-ext create --type app --name my-app
  Creating app extension "my-app"...
    kamiwaza.json
    docker-compose.yml
    frontend/Dockerfile
    frontend/package.json
    frontend/src/app/layout.tsx
    frontend/src/app/page.tsx
    backend/Dockerfile
    backend/requirements.txt
    backend/app/main.py
    images/
    .gitignore
    README.md
  Initializing git repository... done

  Created my-app/

  Next steps:
    cd my-app
    kz-ext dev local       # Run locally with Docker

$ kz-ext create --type tool --name my-tool
  Creating tool extension "my-tool"...
    kamiwaza.json
    docker-compose.yml
    Dockerfile
    requirements.txt
    src/server.py
    .gitignore
    README.md
  Initializing git repository... done

  Created my-tool/

  Next steps:
    cd my-tool
    kz-ext dev local       # Run locally with Docker

$ kz-ext create --type app --name my-app  # Directory exists
Error: Directory "my-app" already exists
  Choose a different name or remove the existing directory.
```

#### CLI States Summary

| Command | States | Display |
|---------|--------|---------|
| login | Success / Auth failure / Connection failure | Styled message + suggestion |
| dev local | Running / No extension / No Docker / Standalone mode | Header info + compose output passthrough |
| validate | Pass / Fail / Pass with warnings | Error/warning list + exit code |
| doctor | All pass / Warnings / Failures | Checklist with PASS/WARN/FAIL per check |
| create | Success / Dir conflict / Invalid name | File list + next steps |

---

## 5. Design Questions FAQ

### Q1: Main components and interactions

See Section 3.1 (Two-Repo Architecture), Section 3.13–3.14 (Architectural Decisions), and Section 3.15 (Responsibility Matrix).

Key new components for `kamiwaza-extensions`:

- **CLI App** (new package, entry point): Typer-based CLI with subcommand routing. Entry point `kz-ext` registered via `pyproject.toml` `[project.scripts]`. Depends on all components below.
- **ConnectionManager** (new module): Manages `~/.kamiwaza/config` (multi-connection metadata) and per-connection token files. Extends existing `FileTokenStore` pattern. Used by `login`, `dev local`, and any command needing API access.
- **DevLocalRunner** (new module): Detects `kamiwaza.json`, builds env var overlay from stored connection, invokes `docker compose up --build` as subprocess with signal forwarding. Depends on ConnectionManager.
- **MetadataValidator** (new module): Port of `validate-metadata.py` logic — validates `kamiwaza.json` schema, required fields, version format, naming conventions. Pure logic, no API calls.
- **ComposeValidator** (new module): Port of `validate-compose.py` logic — checks deployment compatibility (ports, volumes, resources, networks). Pure logic, no API calls.
- **DoctorChecker** (new module): Runs environment diagnostics (Docker, Python, CLI version, connection health, runtime libs). Aggregates pass/fail/warn results.
- **Scaffolder** (new module): `kz-ext create` implementation. Reads bundled templates (app/tool/service), substitutes variables, writes directory structure, initializes git.
- **Bundled Templates** (package data): Static template files for app (Next.js+FastAPI), tool (FastMCP), and service (minimal) scaffolding.

**Interaction flow:**
```
User → CLI App → (subcommand router)
                   ├── login      → ConnectionManager → KamiwazaClient (SDK) → Platform API
                   ├── dev local  → ConnectionManager → DevLocalRunner → docker compose (subprocess)
                   ├── validate   → MetadataValidator + ComposeValidator
                   ├── doctor     → DoctorChecker → ConnectionManager + Docker + system checks
                   └── create     → Scaffolder → Bundled Templates → filesystem
```

### Q2: Core API contracts and data models

**ConnectionConfig (new, local-only):**
```python
@dataclass
class ConnectionConfig:
    name: str                    # Connection identifier (e.g., "default", "staging")
    url: str                     # Kamiwaza API base URL
    active: bool = False         # Whether this is the current connection
    created_at: float = 0.0      # Epoch timestamp
```

**Config file format (`~/.kamiwaza/config`):**
```json
{
    "version": 1,
    "active_connection": "default",
    "connections": {
        "default": {
            "url": "https://my-cluster.kamiwaza.test/api",
            "created_at": 1711900800.0
        },
        "staging": {
            "url": "https://staging.kamiwaza.test/api",
            "created_at": 1711900900.0
        }
    }
}
```

Each connection's token stored at: `~/.kamiwaza/connections/{name}/token.json` (same `StoredToken` format as existing SDK).

**kamiwaza.json schema (adopted from extensions-template, extended):**

No new API endpoints in Phase 1 — all interactions are local (filesystem, Docker subprocess) or reuse existing SDK API calls (`auth.login_with_password`, `auth.create_pat`).

**New SDK-level API calls used (existing, not new):**
| Method | SDK Call | Purpose |
|--------|----------|---------|
| Login | `auth.login_with_password(username, password)` | Authenticate and get token |
| PAT create | `auth.create_pat(PATCreate)` | Create long-lived PAT for stored connection |
| Health check | `client.get("/health")` or similar | Validate connection in `login` and `doctor` |

#### Flow: kz-ext login

1. User runs `kz-ext login https://cluster.kamiwaza.test/api --name staging`
2. CLI prompts for username/password (or uses `--api-key`)
3. CLI constructs `KamiwazaClient(base_url=url)` with `UserPasswordAuthenticator` or `ApiKeyAuthenticator`
4. CLI calls a lightweight API endpoint to validate the connection
5. If using password auth: CLI creates a PAT via `client.auth.create_pat()` for long-lived storage
6. CLI writes connection metadata to `~/.kamiwaza/config`
7. CLI stores PAT in `~/.kamiwaza/connections/staging/token.json`
8. CLI sets `active_connection: "staging"` in config

#### Flow: kz-ext dev local

1. User runs `kz-ext dev local` in an extension directory
2. CLI searches for `kamiwaza.json` at `.` then one level deep
3. CLI reads `kamiwaza.json` to get extension name
4. CLI reads `~/.kamiwaza/config` to find active connection
5. CLI loads PAT from `~/.kamiwaza/connections/{active}/token.json`
6. CLI builds env overlay: `KAMIWAZA_API_URL={url}`, `KAMIWAZA_USE_AUTH=false`, `KAMIWAZA_ENDPOINT={url}/v1`, `KAMIWAZA_APP_NAME={name}`, etc.
7. CLI detects Docker Compose variant (`docker compose` v2 preferred, fallback `docker-compose` v1)
8. CLI runs `docker compose up --build` with merged env, forwarding stdout/stderr
9. On SIGINT/SIGTERM: forwards signal to subprocess for clean shutdown

### Q3: Deployment and infrastructure dependencies

- **Docker + Docker Compose** — Required on developer machine for `dev local` and `create` (to verify scaffolded projects work). Not a platform dependency. CLI detects v1 vs v2.
- **Kamiwaza Platform API** — Required for `login` (auth endpoints) and `doctor` (health check). Must be reachable from developer machine. Existing infrastructure, no changes needed.
- **PyPI** — `kamiwaza-extensions` published as a separate package. CI pipeline needed for build + publish (GitHub Actions).
- **No new server-side infrastructure** — Phase 1 is entirely client-side tooling. No new services, databases, or platform changes.

**Scaling note:** This is a developer CLI tool — "scaling" means supporting many concurrent developer machines, each running independently. No shared state, no coordination needed. The only shared resource is the Kamiwaza Platform API which already handles concurrent clients.

### Q4: External components and interfaces

- **Docker Engine:** Required for `dev local`. Interface: `docker compose up --build` subprocess with env vars. CLI checks availability via `docker info`.
- **Kamiwaza SDK (`kamiwaza-sdk` PyPI):** Already integrated — `KamiwazaClient`, `Authenticator`, `FileTokenStore`, `ExtensionService`. We use: login, PAT creation, health check. No modifications to SDK.
- **kamiwaza-extensions-lib (PyPI v0.1.0):** Not a runtime dependency of the CLI. Referenced in scaffolded `requirements.txt` templates.
- **@kamiwaza-ai/extensions-lib (npm v0.2.0):** Not a runtime dependency. Referenced in scaffolded `package.json` templates.
- **Git:** Used by `kz-ext create` for `git init`. Optional (CLI warns if unavailable but still creates directory).
- **npm/node:** Not required by CLI. Only needed by developers working on scaffolded app-type extensions (frontend).

### Q5: Testing strategy

**Unit tests:**
- `ConnectionManager`: Config read/write, multi-connection CRUD, active switching, token resolution, corrupt file handling
- `MetadataValidator`: All kamiwaza.json validation rules (required fields, version format, naming conventions, kamiwaza_version ranges)
- `ComposeValidator`: All deployment compatibility rules (ports, volumes, build, resources, networks)
- `Scaffolder`: Template variable substitution, correct file generation per type, directory conflict detection
- `DoctorChecker`: Mock system checks, aggregation of pass/fail/warn
- `DevLocalRunner`: Env var overlay construction (mock subprocess, don't actually run Docker)

**Integration tests (marked `@pytest.mark.integration`):**
- `kz-ext login` against a test Kamiwaza instance (requires credentials)
- `kz-ext dev local` with a real extension and Docker (requires Docker)
- `kz-ext create` + `kz-ext validate` on scaffolded output (filesystem only)

**E2E tests:**
- Full flow: `kz-ext create --type app --name test-app` → `cd test-app` → `kz-ext validate` → `kz-ext dev local` (requires Docker + Kamiwaza instance)

**Test infrastructure:**
- Temp directory fixtures for config/scaffolding tests
- Mock `subprocess.run` for Docker calls in unit tests
- CLI runner (Typer's `CliRunner`) for command integration tests

### Q6: Security implications

**Credential storage:**
- PATs stored in `~/.kamiwaza/connections/{name}/token.json` — file permissions set to 600 (owner read/write only)
- Config file `~/.kamiwaza/config` also 600 (contains URLs which may reveal internal infrastructure)
- `~/.kamiwaza/` directory set to 700 on creation
- Passwords never written to disk; only prompted interactively with echo disabled

**No new auth model:**
- `kz-ext login` reuses existing SDK `UserPasswordAuthenticator` + PAT creation
- PATs have the same scope/permissions as the user who created them
- No new token types, no new auth flows

**Defense-in-depth:**
1. **CLI layer:** Password input via `typer.prompt(hide_input=True)` — not echoed, not logged
2. **Filesystem layer:** `chmod 600` on token files, `chmod 700` on config directory
3. **Transport layer:** SDK's `requests.Session` with `KAMIWAZA_VERIFY_SSL=true` by default

**`kz-ext dev local` security:**
- `KAMIWAZA_USE_AUTH=false` is injected — this is intentional for local dev (no platform auth middleware locally)
- Environment variables are passed to docker-compose subprocess, which injects them into containers
- PAT is NOT injected into container env by default (only API URL and connection info). Extensions that need API access should use `KamiwazaClient.from_env()` inside the container, reading `KAMIWAZA_API_URL`.

**Scaffolding security:**
- Generated `requirements.txt` and `package.json` pin to published versions of runtime libs
- No credentials or secrets embedded in scaffolded code
- `.gitignore` includes `.env`, `*.local*`, and `~/.kamiwaza/` patterns

### Q7: Technical risks and open questions

1. **Docker Compose v1/v2 fragmentation** — Two CLI variants with different invocations (`docker-compose` vs `docker compose`). **Mitigation:** Detect at runtime; prefer v2 plugin; fall back to v1; fail with clear message if neither available.

2. **~~CLI framework choice~~** — **RESOLVED.** Typer selected. Already a transitive dependency, type-hint-driven, Rich integration for styled output.

3. **Config file format migration** — If we change the `~/.kamiwaza/config` schema in future versions, old CLIs reading new configs (or vice versa) could fail. **Mitigation:** Include `"version": 1` field in config; validate on read; provide `kz-ext config migrate` if ever needed.

4. **Bundled template maintenance** — Templates ship inside the package. When runtime libs release new versions, templates become stale until the CLI is updated. **Mitigation:** Use compatible version ranges (`>=0.1.0`) not exact pins. For major updates, ship a new CLI version.

5. **Subprocess signal handling on Windows** — `SIGINT`/`SIGTERM` forwarding to docker-compose works differently on Windows vs Unix. **De-risk strategy:** Primary target is macOS/Linux (Docker Desktop). Windows support is best-effort; document limitations.

6. **kamiwaza.json schema drift** — The schema is implicitly defined in validation scripts, not a formal JSON Schema. As it evolves, the CLI and platform may diverge. **Mitigation:** Define a canonical Pydantic model in `kamiwaza-extensions` that serves as the single source of truth. Export a JSON Schema from it for documentation.

7. **Scaffolded template correctness** — Templates include working code (FastAPI main.py, Next.js app). Verifying these remain correct as runtime libs evolve is labor-intensive. **Mitigation:** Add a CI job that scaffolds each template type and runs `docker compose build` to verify they at least compile/build.

---

## 6. Implementation Plan

### 6.1 Milestone Overview

| # | Milestone | Scope | Dependencies | Exit Criteria |
|---|-----------|-------|-------------|---------------|
| M1 | Package + CLI skeleton | Package structure, pyproject.toml, Typer CLI with help/version, CI | None | `pip install -e .` works, `kz-ext --help` prints subcommands, CI passes |
| M2 | Connection management + login | ConnectionManager, `kz-ext login`, config storage | M1 | Can login, store connection, list/switch connections |
| M3 | Validation | MetadataValidator, ComposeValidator, `kz-ext validate` | M1 | Can validate kamiwaza.json + compose files with correct pass/fail |
| M4 | Dev local | DevLocalRunner, `kz-ext dev local` | M2 | Can run extension locally with env injection and clean shutdown |
| M5 | Doctor | DoctorChecker, `kz-ext doctor` | M2, M3 | All diagnostic checks run and report correctly |
| M6 | Scaffolding | Scaffolder, bundled templates, `kz-ext create` | M1, M3 | Can scaffold app/tool/service, output passes `kz-ext validate` |
| M7 | Integration testing + polish | End-to-end flows, error message polish, docs | M2–M6 | Full flow works, README updated, ready for 1.0.0 release |

### 6.2 Milestone Details

#### M1: Package + CLI Skeleton

**Goal:** Installable package with working `kz-ext` entry point and CI pipeline.
**Dependencies:** None
**Exit criteria:** `pip install kamiwaza-extensions` from local source, `kz-ext --help` shows all planned subcommands (stubs OK), `kz-ext --version` prints version, linting and unit tests pass in CI.

##### Tasks

| # | Task | Component | Layer | Size | Dependencies |
|---|------|-----------|-------|------|-------------|
| T1.1 | Create `kamiwaza_extensions/` package directory with `__init__.py`, `__version__` | CLI App | Package | S | — |
| T1.2 | Write `pyproject.toml` with entry point `kz-ext = kamiwaza_extensions.cli:app`, dependency on `kamiwaza-sdk>=0.11.0` and `typer[all]` | CLI App | Package | S | T1.1 |
| T1.3 | Implement `cli.py` with Typer app, global `--verbose`/`--debug` flags, stub subcommands for all 5 commands | CLI App | CLI | M | T1.1 |
| T1.4 | Add error handling wrapper (catch KamiwazaError, format consistently) | CLI App | CLI | S | T1.3 |
| T1.5 | Set up CI: lint (ruff), format check (black), unit test (pytest) for the new package | CLI App | CI | M | T1.2 |
| T1.6 | Add `tests/` directory with test skeleton and conftest.py | CLI App | Test | S | T1.2 |

##### Testing

| Test Type | Scope | Key Scenarios |
|-----------|-------|---------------|
| Unit | CLI App | `--help` output, `--version` output, unknown subcommand error |
| Unit | Error handler | KamiwazaError formatting, unhandled exception with --debug |

##### Documentation

| Artifact | Audience | Content |
|----------|----------|---------|
| Package README.md | Developers | Installation, quick start, command reference (skeleton) |

---

#### M2: Connection Management + Login

**Goal:** Developers can authenticate and manage multiple Kamiwaza connections.
**Dependencies:** M1
**Exit criteria:** `kz-ext login <url>` stores connection + PAT, `--list` shows connections, `--use` switches active, token files have 600 permissions.

##### Tasks

| # | Task | Component | Layer | Size | Dependencies |
|---|------|-----------|-------|------|-------------|
| T2.1 | Implement `ConnectionManager` class: add/remove/list/get_active/set_active/get_token/save_token | ConnectionManager | Business Logic | L | — |
| T2.2 | Implement config file I/O with atomic writes, chmod 600/700 | ConnectionManager | Data | M | T2.1 |
| T2.3 | Implement `login` command: password prompt, `--api-key`, `--name`, `--list`, `--use` | CLI App | CLI | M | T2.1 |
| T2.4 | Integrate with SDK: KamiwazaClient construction, PAT creation via `auth.create_pat()`, connection validation | CLI App | Integration | M | T2.3 |
| T2.5 | Unit tests for ConnectionManager: CRUD operations, corrupt file handling, permission checks | ConnectionManager | Test | M | T2.1 |
| T2.6 | CLI tests for login command using CliRunner | CLI App | Test | M | T2.3 |

##### Testing

| Test Type | Scope | Key Scenarios |
|-----------|-------|---------------|
| Unit | ConnectionManager | Add first connection (becomes active), add named connection, switch active, remove connection, corrupt config recovery, token load/save |
| Unit | Login command | Successful login, --api-key path, --list output, --use switching, auth failure error message |
| Integration | Login flow | Against live Kamiwaza instance (marked `@pytest.mark.live`) |

---

#### M3: Validation

**Goal:** Can validate any extension's kamiwaza.json and compose file.
**Dependencies:** M1
**Exit criteria:** `kz-ext validate` correctly identifies all error/warning conditions from extensions-template validation scripts. Exit code 0 for pass, 1 for errors.

##### Tasks

| # | Task | Component | Layer | Size | Dependencies |
|---|------|-----------|-------|------|-------------|
| T3.1 | Define `KamiwazaMetadata` Pydantic model with all fields and validators | MetadataValidator | Business Logic | M | — |
| T3.2 | Implement metadata validation rules: required fields, version format, naming conventions, kz_ext_version compatibility, preview_image | MetadataValidator | Business Logic | L | T3.1 |
| T3.3 | Implement compose validation rules: ports, volumes, resources, networks, container_name, Dockerfiles | ComposeValidator | Business Logic | L | — |
| T3.4 | Define `ValidationResult` dataclass and human/JSON output formatters | MetadataValidator | CLI | S | T3.2 |
| T3.5 | Implement `validate` command with `--json` flag | CLI App | CLI | S | T3.2, T3.3, T3.4 |
| T3.6 | Port test cases from extensions-template validation scripts | MetadataValidator, ComposeValidator | Test | L | T3.2, T3.3 |

##### Testing

| Test Type | Scope | Key Scenarios |
|-----------|-------|---------------|
| Unit | MetadataValidator | Missing required fields, invalid version, bad naming, kz_ext_version mismatch, valid metadata passes |
| Unit | ComposeValidator | Host ports, bind mounts, missing resources, missing Dockerfiles, clean compose passes |
| Unit | Validate command | Exit code 0 vs 1, --json output format, combined metadata + compose results |

---

#### M4: Dev Local

**Goal:** One-command local development with Kamiwaza SDK connectivity.
**Dependencies:** M2 (needs ConnectionManager for env overlay)
**Exit criteria:** `kz-ext dev local` detects extension, injects correct env vars, runs docker-compose, handles Ctrl+C cleanly. Works in standalone mode without connection.

##### Tasks

| # | Task | Component | Layer | Size | Dependencies |
|---|------|-----------|-------|------|-------------|
| T4.1 | Implement extension detection (kamiwaza.json search at `.` and one level deep) | DevLocalRunner | Business Logic | S | — |
| T4.2 | Implement Docker Compose command detection (v2 plugin vs v1 standalone) | DevLocalRunner | Business Logic | S | — |
| T4.3 | Implement compose file detection (yml/yaml variants, override merging) | DevLocalRunner | Business Logic | S | — |
| T4.4 | Implement env overlay construction from stored connection | DevLocalRunner | Business Logic | M | T4.1 |
| T4.5 | Implement subprocess management with signal forwarding (SIGINT/SIGTERM) | DevLocalRunner | Business Logic | M | T4.2 |
| T4.6 | Implement `dev local` command tying it all together | CLI App | CLI | M | T4.1–T4.5 |
| T4.7 | Unit tests: extension detection, env overlay, compose detection (mock subprocess) | DevLocalRunner | Test | M | T4.1–T4.5 |

##### Testing

| Test Type | Scope | Key Scenarios |
|-----------|-------|---------------|
| Unit | DevLocalRunner | Extension found at root, found one level deep, not found, multiple found, env overlay with/without connection, compose v1/v2 detection |
| Integration | Dev local | Run against real extension with Docker (marked `@pytest.mark.integration`) |

---

#### M5: Doctor

**Goal:** Comprehensive environment diagnostics with actionable fix suggestions.
**Dependencies:** M2 (connection check), M3 (kz_ext_version check)
**Exit criteria:** All checks run correctly, output shows pass/fail/warn per check, exit code reflects critical failures.

##### Tasks

| # | Task | Component | Layer | Size | Dependencies |
|---|------|-----------|-------|------|-------------|
| T5.1 | Implement individual check functions: Python version, Docker, Compose, Docker running | DoctorChecker | Business Logic | M | — |
| T5.2 | Implement connection check (uses ConnectionManager) | DoctorChecker | Business Logic | S | T2.1 |
| T5.3 | Implement extension-specific checks: CLI version compat, runtime libs in requirements/package.json | DoctorChecker | Business Logic | M | — |
| T5.4 | Implement `doctor` command with formatted checklist output | CLI App | CLI | S | T5.1–T5.3 |
| T5.5 | Unit tests: mock subprocess for Docker/Compose checks, mock filesystem for extension checks | DoctorChecker | Test | M | T5.1–T5.3 |

##### Testing

| Test Type | Scope | Key Scenarios |
|-----------|-------|---------------|
| Unit | DoctorChecker | All pass, Docker missing, Docker not running, connection down, CLI version mismatch, runtime lib missing |

---

#### M6: Scaffolding

**Goal:** One-command extension creation with runtime libraries pre-wired.
**Dependencies:** M1 (CLI), M3 (validate scaffolded output)
**Exit criteria:** `kz-ext create --type {app,tool,service} --name X` produces a valid extension directory. Scaffolded output passes `kz-ext validate`.

##### Tasks

| # | Task | Component | Layer | Size | Dependencies |
|---|------|-----------|-------|------|-------------|
| T6.1 | Create bundled template files for app type (kamiwaza.json, docker-compose.yml, frontend/, backend/) | Bundled Templates | Data | L | — |
| T6.2 | Create bundled template files for tool type (kamiwaza.json, docker-compose.yml, Dockerfile, src/server.py) | Bundled Templates | Data | M | — |
| T6.3 | Create bundled template files for service type (kamiwaza.json, docker-compose.yml, Dockerfile) | Bundled Templates | Data | S | — |
| T6.4 | Implement Scaffolder: template loading, variable substitution, directory creation, name validation, git init | Scaffolder | Business Logic | M | T6.1–T6.3 |
| T6.5 | Implement `create` command with `--type` and `--name` options | CLI App | CLI | S | T6.4 |
| T6.6 | Configure `pyproject.toml` to include template files as package data | CLI App | Package | S | T6.1–T6.3 |
| T6.7 | Unit tests: template rendering, name validation, directory conflict, git init | Scaffolder | Test | M | T6.4 |
| T6.8 | Validation test: scaffold each type, run `kz-ext validate` on output | Scaffolder | Test | M | T6.4, T3.2 |

##### Testing

| Test Type | Scope | Key Scenarios |
|-----------|-------|---------------|
| Unit | Scaffolder | App/tool/service creation, name validation (invalid chars, missing prefix), directory exists error, git init success/failure |
| Integration | Scaffold + validate | Create each type, run validate, verify zero errors |

---

#### M7: Integration Testing + Polish

**Goal:** End-to-end flows verified, error messages polished, documentation complete.
**Dependencies:** M2–M6
**Exit criteria:** Full flow (create → validate → dev local) works end-to-end. README has complete command reference. Ready for v1.0.0 tag.

##### Tasks

| # | Task | Component | Layer | Size | Dependencies |
|---|------|-----------|-------|------|-------------|
| T7.1 | Write E2E test: create → validate → dev local flow | All | Test | M | M4, M6 |
| T7.2 | Polish error messages across all commands (consistent format, actionable suggestions) | CLI App | CLI | M | M2–M6 |
| T7.3 | Add CI job: scaffold each template type, run `docker compose build` to verify templates compile | Bundled Templates | CI | M | T6.1–T6.3 |
| T7.4 | Write README with installation, quick start, and full command reference | — | Docs | M | M2–M6 |
| T7.5 | Version bump to 1.0.0, final test pass, tag release | — | Release | S | T7.1–T7.4 |

##### Testing

| Test Type | Scope | Key Scenarios |
|-----------|-------|---------------|
| E2E | Full flow | Create app → validate → dev local (requires Docker) |
| CI | Template verification | Scaffold + docker compose build for each type |

##### Documentation

| Artifact | Audience | Content |
|----------|----------|---------|
| README.md | Extension developers | Install, quick start, all commands with examples |
| CHANGELOG.md | All | v1.0.0 release notes |

### 6.3 Risk-Ordered Delivery Sequence

```
M1 (skeleton) ─────► M2 (login/connections) ─────► M4 (dev local)
       │                                                  │
       ├─────────────► M3 (validation) ─────────────────┐ │
       │                       │                         ▼ ▼
       │                       ├──────────► M5 (doctor)  M7 (polish)
       │                       │                         ▲
       └─────────────► M6 (scaffolding) ────────────────┘
```

**Rationale:**
- **M1 first:** Everything depends on the package skeleton. Zero risk, fast to build.
- **M2 early:** ConnectionManager is a dependency for M4 (dev local) and M5 (doctor). Login is the first command users will run.
- **M3 parallel with M2:** Validation has no dependency on login. Can be built concurrently.
- **M4 after M2:** Dev local is the primary user value. Needs connection manager for env overlay.
- **M5 after M2+M3:** Doctor combines connection checks and version checks.
- **M6 parallel with M4/M5:** Scaffolding depends only on M1 (CLI) and M3 (validate output). Can be built concurrently with dev local.
- **M7 last:** Integration testing and polish requires all other milestones complete.

**De-risking:** M2 and M4 together form the critical path (login → dev local). These are built first because they deliver the core DoD: "A developer can install, login, and run local dev." If scaffolding or doctor slip, the core value is still delivered.

### 6.4 Definition of Done

A milestone is complete when:
- [ ] All tasks are implemented and code-reviewed
- [ ] All specified tests pass (unit, integration where applicable)
- [ ] No P1 bugs remain
- [ ] Error messages follow consistent format (INF-P16)
- [ ] File permissions set correctly for credential files (INF-P2)
- [ ] `kz-ext --help` reflects all implemented commands

---

## 7. Changelog

### v0.1.0 — 2026-03-31

**Initial version** — Created via CreateDesign workflow.

---

## Appendix A: Workstream Overviews

This design covers a single workstream (Phase 1) so Appendix A is consolidated here.

### A1. CLI Foundation + Local Dev (Phase 1)

**Priority:** P1 | **Wave:** 1

Phase 1 delivers the `kamiwaza-extensions` PyPI package with the `kz-ext` CLI entry point and six foundational commands: package skeleton, login, dev local, validate, doctor, and create. This is the prerequisite for all subsequent phases (dev inner loop, seamless redeploy, publishing).

**Key Issues:** ENG-3056, ENG-3057, ENG-3058, ENG-3059, ENG-3060, ENG-3061, ENG-3071

**Dependencies:**
- `kamiwaza-sdk` >= 0.11.0 (authentication, API access)
- `kamiwaza-extensions-lib` v0.1.0 (referenced by scaffolded templates)
- `@kamiwaza-ai/extensions-lib` v0.2.0 (referenced by scaffolded templates)
- Docker + Docker Compose (runtime dependency for `dev local`)

---

## Appendix B: User Acceptance Criteria

### B1. CLI Foundation + Local Dev

#### Package Setup (ENG-3057)

**UAC-1: Install CLI tool**
GIVEN a Python 3.10+ environment
WHEN the developer runs `pip install kamiwaza-extensions`
THEN the `kz-ext` command is available on PATH

**UAC-2: CLI help and version**
GIVEN the `kamiwaza-extensions` package is installed
WHEN the developer runs `kz-ext --help` or `kz-ext --version`
THEN help text with all available subcommands is displayed, or the installed version is printed

**UAC-3: CLI subcommand routing**
GIVEN the `kamiwaza-extensions` package is installed
WHEN the developer runs `kz-ext <subcommand>` for any implemented command
THEN the correct subcommand handler executes with proper argument parsing

#### Login (ENG-3061)

**UAC-4: Interactive login with username/password**
GIVEN the developer has a Kamiwaza instance URL and credentials
WHEN the developer runs `kz-ext login <url>` and enters username/password at the prompt
THEN the CLI authenticates via the Kamiwaza SDK, stores a PAT and connection config in `~/.kamiwaza/config`, and confirms success

**UAC-5: Login with API key**
GIVEN the developer has a Kamiwaza instance URL and a PAT
WHEN the developer runs `kz-ext login <url> --api-key <key>`
THEN the CLI stores the connection config and PAT in `~/.kamiwaza/config` without prompting for credentials

**UAC-6: Named connections**
GIVEN the developer works with multiple Kamiwaza instances
WHEN the developer runs `kz-ext login <url> --name staging`
THEN the connection is stored under the name "staging" and can be referenced later

**UAC-7: List stored connections**
GIVEN one or more connections are stored in `~/.kamiwaza/config`
WHEN the developer runs `kz-ext login --list`
THEN all stored connections are displayed with name, URL, and which is currently active

**UAC-8: Switch active connection**
GIVEN multiple named connections exist
WHEN the developer runs `kz-ext login --use <name>`
THEN the specified connection becomes the active connection for subsequent commands

**UAC-9: Connection validation on login**
GIVEN the developer runs `kz-ext login <url>` with valid credentials
WHEN authentication succeeds
THEN the CLI validates the connection by calling a lightweight API endpoint and reports success or failure

#### Dev Local (ENG-3060)

**UAC-10: Run extension locally**
GIVEN the developer is in a directory containing a `kamiwaza.json` file (at root or one level deep) and has a stored Kamiwaza connection
WHEN the developer runs `kz-ext dev local`
THEN the CLI injects Kamiwaza environment variables (`KAMIWAZA_API_URL`, `KAMIWAZA_ENDPOINT`, `KAMIWAZA_PUBLIC_API_URL`, `KAMIWAZA_USE_AUTH=false`, etc.) and runs `docker-compose up --build`

**UAC-11: Stdout/stderr passthrough**
GIVEN `kz-ext dev local` is running
WHEN docker-compose produces stdout or stderr output
THEN the output is passed through to the developer's terminal in real time

**UAC-12: Clean shutdown**
GIVEN `kz-ext dev local` is running
WHEN the developer presses Ctrl+C
THEN SIGINT/SIGTERM is forwarded to docker-compose for a clean shutdown of all containers

**UAC-13: Standalone mode (no connection)**
GIVEN the developer has no Kamiwaza connection configured
WHEN the developer runs `kz-ext dev local`
THEN docker-compose runs without Kamiwaza environment variables (standalone mode) and the developer is informed that no Kamiwaza connection is active

**UAC-14: Extension detection**
GIVEN the developer is in a directory without a `kamiwaza.json` at root or one level deep
WHEN the developer runs `kz-ext dev local`
THEN the CLI reports an error with a clear message explaining what's needed

#### Validate (ENG-3058)

**UAC-15: Validate kamiwaza.json**
GIVEN the developer is in an extension directory with a `kamiwaza.json`
WHEN the developer runs `kz-ext validate`
THEN the CLI checks schema compliance, required fields, version format, `kz_ext_version`, `kamiwaza_version`, and `preview_image` path, reporting pass/fail with actionable messages

**UAC-16: Validate compose file for deployment compatibility**
GIVEN the extension has a `docker-compose.yml`
WHEN the developer runs `kz-ext validate`
THEN the CLI checks for deployment compatibility issues (host port bindings, bind mounts, missing resource limits) and reports them as warnings (not errors, since these are allowed for local dev)

**UAC-17: Validate Dockerfiles exist**
GIVEN a compose file references services with `build` fields
WHEN the developer runs `kz-ext validate`
THEN the CLI checks that the referenced Dockerfiles exist and reports any missing ones

**UAC-18: Exit code reflects validation result**
GIVEN a validation run completes
WHEN there are errors (not just warnings)
THEN exit code is 1; when all checks pass (warnings OK), exit code is 0

#### Doctor (ENG-3059)

**UAC-19: CLI version compatibility check**
GIVEN the extension has a `kamiwaza.json` with `kz_ext_version` set
WHEN the developer runs `kz-ext doctor`
THEN the CLI checks its own version against the `kz_ext_version` semver range and reports pass/fail

**UAC-20: Docker environment check**
GIVEN the developer runs `kz-ext doctor`
WHEN Docker is not installed, not running, or Docker Compose is unavailable
THEN the CLI reports each issue with an actionable fix suggestion

**UAC-21: Kamiwaza connection check**
GIVEN a Kamiwaza connection is configured
WHEN the developer runs `kz-ext doctor`
THEN the CLI hits the health endpoint and reports whether the connection is live

**UAC-22: Python version check**
GIVEN the developer runs `kz-ext doctor`
WHEN the Python version is below the minimum supported version
THEN the CLI reports a warning or failure with the required version

**UAC-23: Doctor exit code**
GIVEN a doctor run completes
WHEN any critical check fails
THEN exit code is 1; when all pass, exit code is 0

#### Create / Scaffolding (ENG-3071)

**UAC-24: Create app extension**
GIVEN the developer has `kamiwaza-extensions` installed
WHEN the developer runs `kz-ext create --type app --name my-app`
THEN a new `my-app/` directory is created with: `kamiwaza.json` (with `kz_ext_version`), `docker-compose.yml` (with local dev affordances), `frontend/` (Next.js with `@kamiwaza-ai/extensions-lib`), `backend/` (FastAPI with `kamiwaza-extensions-lib`), Dockerfiles with dev and prod stages, and `README.md`

**UAC-25: Create tool extension**
GIVEN the developer has `kamiwaza-extensions` installed
WHEN the developer runs `kz-ext create --type tool --name my-tool`
THEN a new `my-tool/` directory is created with: `kamiwaza.json`, `docker-compose.yml`, `src/server.py` (FastMCP server with example tool), `Dockerfile`, `requirements.txt` (with `kamiwaza-extensions-lib`)

**UAC-26: Create service extension**
GIVEN the developer has `kamiwaza-extensions` installed
WHEN the developer runs `kz-ext create --type service --name my-service`
THEN a new `my-service/` directory is created with: `kamiwaza.json`, `docker-compose.yml`, `Dockerfile`, `README.md`

**UAC-27: Git initialization**
GIVEN `kz-ext create` completes successfully
WHEN the scaffolded directory is created
THEN the directory is initialized as a git repository with a `.gitignore` file

**UAC-28: Runtime library pre-wiring**
GIVEN `kz-ext create --type app` is run
WHEN the scaffolded code is generated
THEN `kamiwaza-extensions-lib` is in the backend `requirements.txt`, `@kamiwaza-ai/extensions-lib` is in the frontend `package.json`, the health endpoint and session router are configured in the generated `main.py`, and `SessionProvider`/`AuthGuard` are wired in the frontend

#### Schema Addition

**UAC-29: kz_ext_version field**
GIVEN a `kamiwaza.json` file
WHEN the `kz_ext_version` field is present
THEN it is validated as a semver range (e.g., `">=1.0.0,<2.0.0"`) and used by `doctor` and `validate` for CLI compatibility checking

#### Inferred Requirements [INFERRED]

**INF-P1: Config directory creation** *(ref: UAC-4)*
GIVEN the `~/.kamiwaza/` directory does not exist
WHEN the developer runs `kz-ext login` for the first time
THEN the CLI creates `~/.kamiwaza/` with appropriate permissions (700) before writing config

**INF-P2: Config file permissions** *(ref: UAC-4, UAC-5)*
GIVEN a PAT is stored in `~/.kamiwaza/config`
WHEN the config file is written
THEN file permissions are set to 600 (owner read/write only) since it contains credentials

**INF-P3: Token refresh for stored connections** *(ref: UAC-4)*
GIVEN a stored connection's PAT has expired
WHEN the developer runs any command that requires API access
THEN the CLI either automatically refreshes the token or prompts the developer to re-authenticate with a clear message

**INF-P4: Connection config backward compatibility with token.json** *(ref: UAC-4)*
GIVEN the existing SDK stores tokens at `~/.kamiwaza/token.json`
WHEN `kz-ext` introduces `~/.kamiwaza/config` for multi-connection storage
THEN the two mechanisms must not conflict — either `kz-ext` migrates existing token.json into the new config, or they coexist cleanly

**INF-P5: Invalid/corrupt config handling** *(ref: UAC-7, UAC-8)*
GIVEN `~/.kamiwaza/config` is corrupted or contains invalid JSON/YAML
WHEN the developer runs any `kz-ext` command that reads config
THEN the CLI reports a clear error with instructions to fix or reset the config, rather than crashing with a stack trace

**INF-P6: Docker Compose version detection** *(ref: UAC-10, UAC-20)*
GIVEN `docker-compose` (v1) and/or `docker compose` (v2 plugin) may be installed
WHEN the CLI needs to invoke Docker Compose
THEN it detects which variant is available and uses the correct invocation, preferring v2

**INF-P7: Compose file location flexibility** *(ref: UAC-10)*
GIVEN extensions may use `docker-compose.yml`, `docker-compose.yaml`, or `compose.yml`
WHEN `kz-ext dev local` searches for the compose file
THEN it checks standard filenames in order and uses the first found

**INF-P8: Extension name validation on create** *(ref: UAC-24, UAC-25, UAC-26)*
GIVEN the developer provides an extension name via `--name`
WHEN the name contains invalid characters (spaces, special chars) or conflicts with conventions (tool names must start with `tool-` or `mcp-`, service names with `service-`)
THEN the CLI either auto-applies the convention prefix or reports a clear validation error

**INF-P9: Directory conflict on create** *(ref: UAC-24, UAC-25, UAC-26)*
GIVEN the developer runs `kz-ext create --name my-app`
WHEN a directory named `my-app/` already exists in the current directory
THEN the CLI refuses to overwrite and reports a clear error, rather than silently clobbering existing files

**INF-P10: Validate reports structured output** *(ref: UAC-15, UAC-16, UAC-17)*
GIVEN CI/CD systems may consume validation results
WHEN `kz-ext validate` runs
THEN results should be human-readable by default with an optional `--json` flag for machine-readable output

**INF-P11: Offline mode for dev local** *(ref: UAC-10, UAC-13)*
GIVEN the developer's machine has no network access to the Kamiwaza instance
WHEN `kz-ext dev local` is run
THEN containers start normally (docker-compose doesn't depend on Kamiwaza API availability), even if the injected env vars point to an unreachable host

**INF-P12: Graceful degradation when Docker is not running** *(ref: UAC-10)*
GIVEN Docker daemon is not running
WHEN the developer runs `kz-ext dev local`
THEN the CLI detects this before attempting docker-compose and reports an actionable error (e.g., "Docker is not running. Start Docker Desktop or run `sudo systemctl start docker`")

**INF-P13: Multiple compose files** *(ref: UAC-10)*
GIVEN an extension may have a `docker-compose.override.yml` for local dev customization
WHEN `kz-ext dev local` runs
THEN it respects standard Docker Compose file merging behavior (base + override)

**INF-P14: Validate warns on missing optional fields** *(ref: UAC-15)*
GIVEN `kamiwaza.json` is missing optional but recommended fields (e.g., `description`, `preview_image`)
WHEN `kz-ext validate` runs
THEN these are reported as warnings, not errors

**INF-P15: Create sets initial version** *(ref: UAC-24, UAC-25, UAC-26)*
GIVEN `kz-ext create` scaffolds a new extension
WHEN the `kamiwaza.json` is generated
THEN the `version` field is set to `"0.1.0"` and `kz_ext_version` is set to a range compatible with the installed CLI version

**INF-P16: Error reporting consistency** *(ref: all UACs)*
GIVEN any `kz-ext` command encounters an error
WHEN the error is displayed
THEN it follows a consistent format: error type, message, and suggested fix — no raw Python tracebacks unless `--verbose`/`--debug` is set

**INF-P17: Login credential security** *(ref: UAC-4, UAC-5)*
GIVEN the developer enters a password at the prompt
WHEN the password is read
THEN it is not echoed to the terminal and is not logged or stored in shell history

**INF-P18: Validate checks kz_ext_version against installed CLI** *(ref: UAC-15, UAC-29)*
GIVEN `kamiwaza.json` has a `kz_ext_version` field
WHEN `kz-ext validate` runs
THEN it checks the installed CLI version against the semver range and warns if incompatible

---
