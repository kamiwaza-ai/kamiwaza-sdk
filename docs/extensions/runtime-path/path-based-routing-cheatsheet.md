# Path-Based Routing Cheatsheet for Kamiwaza Apps

> **SDK scaffold update required**
>
> `kamiwaza-extensions-template` and Copier are deprecated. The canonical
> scaffold is bundled in `kamiwaza-sdk` and rendered by `kz-ext create`.
> To bring an existing SDK-scaffolded app forward, first install an SDK build
> whose app Dockerfile contains `index-next-runtime.mjs`. Verify the installed
> scaffold before updating:
> ```bash
> python -c 'from importlib.metadata import distribution; p=distribution("kamiwaza-sdk").locate_file("kamiwaza_extensions/templates/app/frontend/Dockerfile"); assert "index-next-runtime.mjs" in p.read_text()'
> ```
> Only after that check succeeds, run from the extension:
> ```bash
> cd my-app
> kz-ext update
> ```
> The SDK app scaffold
> (`kamiwaza_extensions/templates/app/frontend/Dockerfile`) is the source of
> truth for the **dual-artifact Next.js runtime**: the exact
> `next@15.5.19` pin, the `withKamiwazaAppGarden()` config wrapper, the boot
> relocation entrypoint, and the `0.5.x` runtime libraries
> (`@kamiwaza-ai/extensions-lib` / `kamiwaza-extensions-lib`). Apps scaffolded
> before this change ran `next build` **at every spawn** (minutes of startup,
> GBs of memory); everything in this document assumes that rebuild is gone.

This guide explains how apps serve under both port-based and path-based routing in Kamiwaza — and what you, as an app author, must (and must not) do about it.

## Overview

Kamiwaza deploys apps in two modes:

| Mode | URL Pattern | Selected By |
|------|-------------|-------------|
| **Port-based** | `https://host:PORT/` | `KAMIWAZA_ROUTING_MODE=port` |
| **Path-based** | `https://host/runtime/apps/{id}/...` | `KAMIWAZA_ROUTING_MODE=path` + `KAMIWAZA_APP_PATH` |

If `KAMIWAZA_ROUTING_MODE` is unset, a nonempty `KAMIWAZA_APP_PATH` implies path mode (backward compatibility). The same image serves both modes — no rebuild, no per-deployment image.

Key facts about path mode:

- **The public app frontend receives the full, un-stripped prefix.**
  `strip_path_prefix=false` remains the app default in `kamiwaza.json`, so
  Traefik forwards the deployment path to the relocated Next.js server. The
  shared Next route proxy then removes that prefix before forwarding `/api/*`
  and auth/session calls to the internal FastAPI backend.
- **Trailing-slash canonicalization**: `GET <prefix>/` responds `308` redirecting to `<prefix>` (Next's base-path canonicalization). Expected; don't "fix" it.
- The deployment prefix is applied **at container start** by byte relocation of a prebuilt artifact — never by a runtime `next build`, and never via `NEXT_PUBLIC_APP_BASE_PATH` (that variable is gone; do not reintroduce it).

### Services and Tools

Services and tools also use path-based routing, with different prefixes and proxy behavior:

- Services: `/runtime/services/{id}`; Tools: `/runtime/tools/{id}`
- For services and tools the prefix is **stripped** before forwarding by
  default; an app's public frontend receives the full prefix. Override the
  public ingress behavior per template with `"strip_path_prefix"` in
  `kamiwaza.json` (`true` = strip, `false` = forward).
- Services/tools should serve at `/` and use `X-Forwarded-Prefix` only when constructing links.
- `KAMIWAZA_APP_PATH` is still set for services (it will contain `/runtime/services/{id}`).

---

## 1. How It Works: One Image, Two Prebuilt Artifacts

Next.js bakes `basePath`/`assetPrefix` at build time, but the deployment prefix is only known at spawn. The old answer was rebuilding at spawn; the new answer is building **twice at image time** and **relocating bytes at boot**:

```
                    docker build (CI — once per release)
┌───────────────────────────────────────────────────────────────────────┐
│ deps ─┬─► build-port  KZ_NEXT_BUILD_VARIANT=port                      │
│       │     next build, basePath ""            ──► /app/runtime/port  │
│       └─► build-path  KZ_NEXT_BUILD_VARIANT=path                      │
│             next build, basePath =                                    │
│             /__KZ_RUNTIME_BASE_7F3A91C2__      ──► /app/runtime/path  │
│                                                                       │
│ index-next-runtime.mjs (fail-closed scan of the path artifact)        │
│                            ──► /app/runtime/kz-next-relocations.json  │
└───────────────────────────────────────────────────────────────────────┘
                    container start (every spawn — milliseconds)
┌───────────────────────────────────────────────────────────────────────┐
│ start-next-runtime.mjs (ENTRYPOINT)                                   │
│   port mode ──► node /app/runtime/port/server.js  (as-is, no copy)    │
│   path mode ──► verify manifest (hashes, counts, Next version)        │
│                 mirror to /tmp/kz-next-runtime:                       │
│                   • symlink unchanged files (+ node_modules, public)  │
│                   • copy + byte-patch indexed files                   │
│                     sentinel ──► validated KAMIWAZA_APP_PATH          │
│                 verify: JSON parses, totals match, zero residual      │
│                 sentinel ──► atomic publish ──► node server.js        │
└───────────────────────────────────────────────────────────────────────┘
```

The final `runner` image contains only the two artifacts, the relocation manifest, and two stdlib-Node scripts. There is **no** app source, npm, TypeScript, or dev dependency tree in the runner.

Each `next build` result must be assembled with all three production surfaces:

```bash
mkdir -p "/out/$variant/.next/static"
cp -R .next/standalone/. "/out/$variant/"
cp -R .next/static/. "/out/$variant/.next/static/"
[ ! -d public ] || cp -R public "/out/$variant/public"
```

Copying `.next/standalone` alone is incomplete: it omits client chunks and
verbatim `public/` assets. Preserve the compatible SDK scaffold's
`build-port`, `build-path`, `runtime-assembly`, and `runner` stages rather than
maintaining an abbreviated second recipe.

**Measured cost** (canary against real Next 15.5.19): relocation takes **~24 ms at ~53 MiB RSS**. The old spawn-time rebuild took minutes and 1.5–3 GB. The host canary allows 10 seconds for boot-to-health and enforces the runtime's reported preparation ≤ 5,000 ms and preparation RSS ≤ 96 MiB. The scaffold defaults the frontend memory limit to 512 MiB; repository validation requires a limit to be present but does not enforce that exact value.

---

## 2. The Sentinel and the Fail-Closed Relocation Lifecycle

The path-variant build uses the reserved base path sentinel:

```
/__KZ_RUNTIME_BASE_7F3A91C2__
```

(exported as `KAMIWAZA_BASE_PATH_SENTINEL` from `@kamiwaza-ai/extensions-lib/next-config`). Every file that embeds it — `server.js`, `.next` manifests (including serialized middleware matchers), prerendered HTML, RSC/Flight payloads, client chunks, CSS, redirect `.meta` files — gets rewritten at boot to the real prefix.

The lifecycle is fail-closed at every stage:

| Stage | What happens | Fails the build/boot if… |
|-------|--------------|--------------------------|
| **Build** | Two `next build` runs (`port`, `path` variants) assembled as standalone artifacts | Next version isn't exactly `15.5.19`; wrapper-rejected options are set |
| **Index** (image build) | `index-next-runtime.mjs` records every sentinel-bearing file: path, size, SHA-256, occurrence count, kind (`js`/`json`/`html`/`rsc`/`css`/`txt`) | Sentinel appears in a binary/unrecognized file, in `node_modules`, or under `public/`; a source map ships outside `node_modules`; broken/directory/root-escaping symlinks; `.next/cache` present; a mandatory role (server.js, server config, client chunks) has zero occurrences |
| **Boot verify** | Manifest schema, per-file SHA-256 + occurrence counts, artifact Next version re-checked against the manifest | Any hash/count/version mismatch — the artifact no longer matches its index |
| **Patch** | Sparse mirror into `/tmp`: indexed files copied + byte-replaced (Flight-frame-aware for `.rsc`, byte-length headers recomputed), everything else symlinked, `server.js` always copied | Patched JSON doesn't parse; sentinel inside an unsupported Flight row type |
| **Scan** | Full walk of the staged tree | Any residual sentinel byte; patched-file/occurrence totals don't match the manifest |
| **Atomic publish** | Staging dir renamed over the target only after all checks pass; per-target lock prevents concurrent starts | Lock held by a live process (stale locks from dead pids are stolen) |

If any check fails, the container **refuses to start** — you never get a half-relocated app.

The runtime path itself is validated with a conservative grammar before anything runs: absolute, no trailing slash, segments matching `[A-Za-z0-9._~-]+`, no `%`, `?`, `#`, `\`, `//`, dot segments, or control characters. Platform-generated `/runtime/apps/<uuid>` prefixes always pass; anything else is treated as misconfiguration.

---

## 3. Environment Variables

The platform (and the generated compose) threads the routing env to **every extension-owned service** — frontend, backend, and any workers:

```bash
KAMIWAZA_ROUTING_MODE="path"                     # or "port"
KAMIWAZA_APP_PATH="/runtime/apps/{uuid}"         # deployment prefix (path mode)
KAMIWAZA_APP_PATH_URL="https://host/runtime/apps/{uuid}"  # full public URL, path mode
KAMIWAZA_APP_URL="https://host:PORT"             # full public URL, port mode
KAMIWAZA_APP_PORT="30123"
KAMIWAZA_DEPLOYMENT_ID="550e8400-..."
KAMIWAZA_ORIGIN="https://host"                   # optional origin fallback
```

Rules:

- **Env is authoritative.** `x-forwarded-prefix` is forwarded and may be logged/compared for diagnostics, but it never selects the app path.
- **Do not set `NEXT_PUBLIC_APP_BASE_PATH`** anywhere — compose, metadata, or code. The wrapper defines a reserved internal compile constant instead; author-facing base-path env is gone.
- `BACKEND_URL=http://backend:8000` stays internal and unprefixed.

---

## 4. Writing App Code (Next.js Frontend)

### next.config.js — the wrapper owns the base path

```js
const { withKamiwazaAppGarden } = require("@kamiwaza-ai/extensions-lib/next-config");

module.exports = withKamiwazaAppGarden({
  output: "standalone",
});
```

`withKamiwazaAppGarden()` selects the build variant from `KZ_NEXT_BUILD_VARIANT` (set by the Dockerfile; ignored under `next dev`), owns `basePath`/`assetPrefix`, pins the supported Next version, and **throws** on incompatible options (see [Unsupported in V1](#9-unsupported-in-v1)). Never set `basePath` or `assetPrefix` yourself.

### Root layout — inline bootstrap + prefixed metadata icons

```tsx
import { appAsset, KamiwazaRuntimeBootstrap } from "@kamiwaza-ai/extensions-lib/runtime";

export const metadata = {
  icons: { icon: appAsset("/kmza-icon.png") },  // NOT "/kmza-icon.png"
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <head>
        <KamiwazaRuntimeBootstrap />
      </head>
      <body>{children}</body>
    </html>
  );
}
```

`<KamiwazaRuntimeBootstrap />` inlines only `{ routingMode, appPath }` as `globalThis.__KAMIWAZA_RUNTIME__` — in the path artifact the inlined `appPath` is the sentinel, so relocation corrects it. Keep the layout statically prerenderable: don't call `headers()` or read deployment env there. It accepts an optional `nonce` prop for CSP setups.

### What just works (no helper needed)

Next rewrites these through the relocated `basePath` automatically:

- `<Link href="/chat">`, `router.push('/chat')`, `router.replace(...)`
- `redirect()` / route-level redirects
- `next/font`, CSS chunks, `/_next/*` asset loading
- Statically **imported** assets (`import logo from './logo.png'`)

### What needs a helper

| Need | Helper | Import from |
|------|--------|-------------|
| `fetch()` same-app routes | `appFetch(input, init?)` | `@kamiwaza-ai/extensions-lib/runtime` |
| `public/` asset by string path | `appAsset(path)` | `@kamiwaza-ai/extensions-lib/runtime` |
| Raw prefix (WebSocket/EventSource URLs, `window.location`) | `getAppPath()`, `withAppPath(path, appPath?)` | `@kamiwaza-ai/extensions-lib/runtime` |
| Full deployment details (id, public URLs) | `loadKamiwazaRuntime()` | `@kamiwaza-ai/extensions-lib/runtime` |
| Server-side runtime config | `getKamiwazaRuntimeServer(env?, forwardedPrefix?)` | `@kamiwaza-ai/extensions-lib/runtime/server` |

```typescript
import { appFetch, appAsset, getAppPath, loadKamiwazaRuntime } from "@kamiwaza-ai/extensions-lib/runtime";

// ❌ WRONG — misses the deployment prefix in path mode
const res = await fetch("/api/models");

// ✅ CORRECT — prefixes root-relative same-app paths; idempotent
const res = await appFetch("/api/models");

// ✅ public/ assets referenced by string (next/image included)
<Image src={appAsset("/hero.png")} alt="" width={800} height={400} />

// ✅ WebSocket under the prefix
const wsScheme = location.protocol === "https:" ? "wss:" : "ws:";
const ws = new WebSocket(`${wsScheme}//${location.host}${getAppPath()}/api/ws`);

// ✅ Deployment details, lazily (memoized; nothing calls this by default)
const { deploymentId, appUrl } = await loadKamiwazaRuntime();
```

Notes:

- `getAppPath()` is synchronous and pre-hydration-safe: it reads the inline bootstrap in the browser and falls back to the build-variant compile constant on the server (where the sentinel value is corrected by relocation). This makes `appAsset()` safe in static Server Components.
- `appFetch` does **not** monkey-patch global `fetch`. It prefixes root-relative
  string inputs and same-origin `URL` objects. Absolute/protocol-relative
  strings, external `URL` objects, and `Request` objects pass through.
- `loadKamiwazaRuntime()` fetches the scaffold's no-store JSON route **`/kamiwaza/runtime.json`** (a normal App Router route, for example `app/kamiwaza/runtime.json/route.ts` or `src/app/kamiwaza/runtime.json/route.ts` — note: *not* `__kamiwaza`; leading-underscore folders are private in the App Router and never routed). The response contains only non-secret routing fields: `routingMode`, `appPath`, `appUrl`, `appPathUrl`, `deploymentId`, `appPort`.
- Implement that route with the server-only helper:

```typescript
import { createRuntimeConfigResponse } from
  "@kamiwaza-ai/extensions-lib/runtime/server";

export const dynamic = "force-dynamic";

export function GET() {
  return createRuntimeConfigResponse();
}
```

- The scaffold's API proxy needs no path logic — the shared proxy strips the runtime app path automatically:

```typescript
// src/app/api/[...path]/route.ts
import { createProxyHandlers } from "@kamiwaza-ai/extensions-lib/server";

const { GET, POST, PUT, PATCH, DELETE } = createProxyHandlers({
  target: process.env.BACKEND_URL || "http://backend:8000",
});
```

---

## 5. Known Limitation: Raw Public-Root Asset Strings

Bare string references to `public/` files are **not promised** to be prefixed:

```tsx
// ❌ NOT SUPPORTED in path mode — served from the wrong URL
<Image src="/photo.png" alt="" width={100} height={100} />
<div style={{ backgroundImage: "url('/bg.png')" }} />

// ✅ Static import (preferred — hashed, prefixed, typed)
import photo from "@/assets/photo.png";
<Image src={photo} alt="" />

// ✅ appAsset() for public/-root strings
<Image src={appAsset("/photo.png")} alt="" width={100} height={100} />
```

This is not a regression: Next's `basePath` **never rewrote raw public-root strings** — such references were already broken under the old runtime-rebuild scheme. The relocation indexer enforces the discipline at build time: a sentinel-bearing file under `public/` fails the image build (public assets are served verbatim and never patched).

---

## 6. FastAPI Backend

The backend runs behind the shared ASGI launcher instead of a bare `uvicorn` command:

```dockerfile
# backend/Dockerfile
CMD ["python", "-m", "kamiwaza_extensions_lib.asgi", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

The generated app exposes the Next frontend at public ingress. Its shared
route proxy removes the deployment prefix before forwarding to the internal
backend. The backend launcher resolves the same routing env and starts Uvicorn
with `root_path=<KAMIWAZA_APP_PATH>` (empty in port mode), preserving the
external mount context while the backend declares ordinary routes.

- **Declare routes unprefixed** — `/health`, `/session`, `/auth/*`, `/api/*` —
  exactly as before. The frontend proxy target and direct container health
  probes use those unprefixed paths.
- **URL generation sees the prefix** — `request.url_for(...)`, OpenAPI `servers`, and redirects come out prefixed for free.
- **No app-level prefix routers, no path-rewriting middleware.** Delete them if you have them.
- **Do not expose a second public route directly to the backend with the full
  prefix.** The supported scaffold topology enters through the frontend;
  forwarding an already-prefixed path to Uvicorn while also setting
  `root_path` can duplicate the prefix.
- **Cookies** use `RuntimeRouting.cookie_path` — the app prefix in path mode, `/` in port mode — so two deployments on the same host can't shadow each other's cookies:

```python
from kamiwaza_extensions_lib import RuntimeRouting, normalize_app_path, with_app_path

routing = RuntimeRouting.from_env()
routing.routing_mode   # "path" | "port"
routing.app_path       # "/runtime/apps/{uuid}" or ""
routing.root_path      # ASGI root_path (same as app_path)
routing.cookie_path    # app_path or "/"
routing.app_url        # canonical public URL (see precedence below)
```

`normalize_app_path()` / `with_app_path()` mirror the TypeScript helpers byte-for-byte (both implementations are tested against one shared vector file).

---

## 7. Auth URLs and Cookie Isolation

### Public URL precedence

When the app (or the shared auth libraries) needs its own public URL — login `return_to`, logout redirects, OAuth callbacks:

| Mode | Precedence |
|------|-----------|
| **path** | `KAMIWAZA_APP_PATH_URL` → `KAMIWAZA_APP_URL` → `KAMIWAZA_ORIGIN + appPath` |
| **port** | `KAMIWAZA_APP_URL` |

**Env is authoritative.** `x-forwarded-prefix` is corroboration only — both the TS (`getKamiwazaRuntimeServer`) and Python (`RuntimeRouting`) implementations log a warning on mismatch but never let the header override env. In the browser, when a full URL is needed, compute `window.location.origin + getAppPath()`.

### Cookies

- Extension-set cookies use `Path=<appPath>` in path mode and `Path=/` in port mode (`RuntimeRouting.cookie_path`), so deployments under different prefixes stay isolated.
- The shared Next proxy **drops `Set-Cookie` by default** and passes it through only for the configured session-route allowlist. Its default list is `/session`, the reserved compatibility path `/session/extend`, and `/auth/logout`; the canonical Python session router does not create `/session/extend`.
- `SessionProvider` from `@kamiwaza-ai/extensions-lib/client` derives its base
  path from `getAppPath()` automatically; the explicit `basePath` prop remains
  as a deprecated escape hatch. The frozen `@kamiwaza/auth@0.2.0` package
  still has its legacy cookie-era semantics and is not a runtime re-export.

See the [Auth Integration Guide](../auth-integration-guide.md) for the full login/logout/session wiring.

---

## 8. Read-Only Root Filesystem and /tmp

The production runner is compatible with `--read-only` and runs as a non-root user (uid 1001):

- **`/tmp` is the only writable location.** Path mode builds the relocated tree at `/tmp/kz-next-runtime`; the writable `.next/cache` lives inside that overlay. Port mode runs the image artifact in place (no copy) and needs no writable app tree.
- **Startup lock**: a per-target lock directory (`/tmp/kz-next-runtime.lock`, holding the owner pid) makes a second concurrent start fail deterministically instead of corrupting the live tree. Locks from dead processes are stolen automatically.
- **Atomic publish**: relocation stages into `/tmp/kz-next-runtime.staging-<pid>` and `rename()`s over the target only after all verification passes.
- ISR disk flush is disabled by the wrapper (`experimental.isrFlushToDisk: false`) so nothing tries to write into the read-only image tree.

Deploying with `--read-only --tmpfs /tmp` is supported by the production
runner. The host canary exercises Next output formats and fail-closed behavior;
the generated-scaffold Docker canary in
`tests/integration/test_scaffolded_next_runtime.py` additionally boots the
production image with a read-only root and `/tmp` tmpfs.

---

## Model Endpoints Are Separate

Runtime app-path relocation does not change model endpoint selection. Endpoint
hosts must match their audience:

- The shipped TypeScript client constructs browser-facing endpoints from its
  public base origin, preferring `access_path` and falling back to `lb_port`.
- Python `list_available_models()` returns browser-facing endpoints for UI
  display. It preserves an explicit platform `endpoint`; otherwise it resolves
  `access_path`, then `lb_port`, against the public base.
- Python `get_model_client()` runs inside the backend container. It prefers an
  explicit platform `endpoint` but rehosts it onto the container-routable
  origin, then falls back to `access_path`, `lb_port`, and the configured
  OpenAI base.

Do not connect directly to a model worker port; the load balancer owns routing
and failover. The generated scaffold demonstrates the backend flow when it
constructs an `AsyncOpenAI` client.

---

## 9. Unsupported in V1

The relocation contract patches bytes; anything that fingerprints or duplicates those bytes is rejected. `withKamiwazaAppGarden()` **throws at build time** on:

| Option | Why it's incompatible |
|--------|----------------------|
| author `basePath` / `assetPrefix` | The wrapper owns them; a custom path `assetPrefix` would bypass relocation |
| `experimental.sri` | Subresource integrity hashes would reject the patched chunks |
| `productionBrowserSourceMaps` | Shipped source maps desynchronize from patched output (and may not ship in the runner at all) |
| `experimental.serverSourceMaps` | Same — production app source maps are forbidden in the artifact |
| `experimental.manualClientBasePath` | Conflicts with the managed base-path contract |
| `env.KZ_INTERNAL_BAKED_APP_PATH` | Reserved compile constant |

Additionally unsupported in V1 (excluded by validation and the indexer's fail-closed scan rather than a wrapper check):

- **PWA / service workers** (`next-pwa` etc.) — precached URLs and worker scope don't survive relocation
- **Multi-zone setups**
- Any Next version other than the exact supported pin (see below)

The indexer independently fails the image build on source maps outside `node_modules`, sentinel bytes in binary/unknown files, and sentinel bytes under `public/`.

Middleware matchers should be authored as normal root-relative Next matchers;
do not splice a deployment prefix into `middleware.ts`. The path build
serializes the sentinel-prefixed matcher into Next's manifests and the boot
relocator rewrites it. The canary verifies both a positive page match and a
non-special-cased excluded route.

---

## 10. Troubleshooting

### Boot events

The entrypoint emits single-line JSON events tagged `kz_next_runtime`:

```json
{"event":"kz_next_runtime","mode":"port","action":"start-native"}
{"event":"kz_next_runtime","mode":"path","appPath":"/runtime/apps/550e...","prepare_ms":24,"prepare_rss_mib":53,"copied_bytes":55574528,"patched_files":42,"occurrences":311}
{"event":"kz_next_runtime","severity":"critical","error":"..."}
```

`prepare_ms` and `prepare_rss_mib` are the cold-start gate numbers; `copied_bytes` shows how sparse the mirror was (only indexed files plus `server.js` are physically copied — everything else is symlinked).

### Fail-closed error meanings

| Error contains | Meaning | Usual cause / fix |
|----------------|---------|-------------------|
| `relocation source hash mismatch` / `occurrence count mismatch` | Artifact no longer matches its relocation index | Image was tampered with or assembled from mixed builds — rebuild the image; never hand-edit `/app/runtime` |
| `relocation totals mismatch: patched X/Y files` | Manifest lists files the mirror couldn't patch | Artifact/manifest drift — rebuild the image |
| `residual sentinel found in …` | A sentinel byte survived patching | File appeared after indexing, or an unindexed encoding — rebuild; report if reproducible |
| `another start (pid N) holds the runtime lock` | Concurrent boot against the same target | Second container/process racing the first; stale locks from dead pids are stolen automatically |
| `patched JSON does not parse` | Replacement corrupted a JSON file | Fail-closed guard — rebuild; report if reproducible |
| `invalid runtime path segment` / `forbidden characters` / `control characters` | `KAMIWAZA_APP_PATH` failed validation | Fix the env value (watch for trailing newlines and URL-encoded chars) |
| `KAMIWAZA_ROUTING_MODE=path requires a nonempty KAMIWAZA_APP_PATH` | Explicit path mode without a path | Fix the deployment env |
| `artifact next@X does not match relocation manifest next@Y` | Version drift between build stages | Rebuild the image from one lockfile |
| `Next A.B.C is not validated for runtime relocation` | Build-time pin violation | See version policy below |

### Next version pin policy

Next.js is pinned **exactly** (`15.5.19` — no `^`/`~`). The relocation contract is validated per-version: `withKamiwazaAppGarden()` refuses production build variants on any other version, and the indexer cross-checks the artifact's traced `next` package against the manifest.

**To bump the pin**: update the version, then run the canary — a version bump is acceptable only when it passes:

```bash
# Builds and packs the TypeScript runtime from this SDK checkout by default:
scripts/test-next-runtime-canary.sh

# Or test an explicit tarball or registry version:
scripts/test-next-runtime-canary.sh --extlib ./kamiwaza-ai-extensions-lib-0.5.0.tgz
```

The canary builds both variants of the fixture (`tests/next-runtime-canary/`),
indexes the path artifact, proves malformed paths and tampered sources fail
closed, boots path mode under a real `/runtime/apps/<uuid>` prefix (asserting
pages, redirect `.meta`, RSC flight, chunks, middleware matching, health,
`runtime.json`, zero sentinel leakage, and the cold-start gates), then boots
port mode from the native artifact and asserts both root-relative assets and
that no relocation copy was created.

`KZ_RUNTIME_IMAGE_ROOT` and `KZ_RUNTIME_TARGET` are internal
launcher/canary overrides for selecting an assembled artifact root and a
writable relocation target. Generated production images set them in the
entrypoint; application code should not depend on them.

---

## 11. Local Development

| Flow | What happens | Relocation? |
|------|--------------|-------------|
| `next dev` | Native no-base development. `KZ_NEXT_BUILD_VARIANT` is unset — and ignored with a warning if it leaks in via `.env` | Never |
| `kz-ext dev local` | Uses the Dockerfile's `dev` stage automatically (compose override: source mount + `next dev` hot reload). Port mode, local auth bridge intact | Never |
| `kz-ext dev --no-push`, publish, App Garden | Production dual-artifact `runner` image | Path mode only |
| `scripts/test-next-runtime-canary.sh` | Host-level fixture for relocation and both boot modes | Yes |

```bash
# Port mode locally (the normal dev loop)
kz-ext dev local
# Access at http://localhost:3000/

# Exercise the real relocation pipeline locally against this checkout
make test-next-runtime-canary
```

There is no local "rebuild with a path" step anymore. If you need to eyeball path-mode behavior during authoring, the canary is the supported route; ordinary development doesn't require it.

---

## Quick Reference: What Goes Where

| Item | Where | Example |
|------|-------|---------|
| Base path config | `next.config.js` | `withKamiwazaAppGarden({ output: "standalone" })` — never set `basePath` yourself |
| Client bootstrap | root `layout.tsx` `<head>` | `<KamiwazaRuntimeBootstrap />` |
| Client fetch | `appFetch()` | `appFetch('/api/models')` |
| `public/` asset string | `appAsset()` | `<Image src={appAsset('/logo.png')} …/>` |
| Bundled asset | static import | `import logo from './logo.png'` |
| Raw prefix | `getAppPath()` / `withAppPath()` | WebSocket / EventSource URLs |
| Deployment details | `loadKamiwazaRuntime()` | fetches `/kamiwaza/runtime.json` (no-store, lazy) |
| Server runtime config | `getKamiwazaRuntimeServer()` | `@kamiwaza-ai/extensions-lib/runtime/server` |
| Backend launcher | `backend/Dockerfile` | `python -m kamiwaza_extensions_lib.asgi app.main:app --host 0.0.0.0 --port 8000` |
| Backend routing/paths | `RuntimeRouting.from_env()` | `.root_path`, `.cookie_path`, `.app_url` |
| Frontend→backend proxy | `createProxyHandlers({ target })` | runtime prefix stripped automatically |

## Summary

1. **Don't manage the base path.** `withKamiwazaAppGarden()` + the boot relocator own it end-to-end; there is no entrypoint rebuild and no `NEXT_PUBLIC_APP_BASE_PATH`.
2. **Use the helpers for the two things Next can't rewrite**: `appFetch()` for fetch calls, `appAsset()`/static imports for `public/` assets.
3. **Backends declare unprefixed routes** and start via
   `python -m kamiwaza_extensions_lib.asgi`; the shared frontend proxy strips
   the deployment prefix before forwarding while `root_path` retains the
   external mount context.
4. **Trust env, not headers**, for deployment identity (`KAMIWAZA_APP_PATH_URL` > `KAMIWAZA_APP_URL` in path mode).
5. **Failures are loud**: a bad artifact or bad prefix stops the container at boot with a `kz_next_runtime` error event — check that JSON line first.
6. **Next is pinned exactly**; version bumps go through `scripts/test-next-runtime-canary.sh`.
