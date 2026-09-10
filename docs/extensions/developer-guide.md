# Kamiwaza extensions — developer guide

> Audience: extension authors using `kz-ext`. For non-Python/TS authors,
> start with [`non-sdk-flow.md`](./non-sdk-flow.md) instead.

## What runs where

`kz-ext` populates two distinct surfaces in the platform. Knowing which
command targets which avoids the most common "I deployed but my colleague
can't see it" confusion.

```
                        ┌─────────────────────────────┐
                        │   kz-ext create --type X    │
                        │  (scaffold a new extension) │
                        └──────────────┬──────────────┘
                                       │
            ┌──────────────────────────┴──────────────────────────┐
            │                                                     │
            ▼                                                     ▼
   ┌─────────────────────┐                            ┌─────────────────────┐
   │   kz-ext dev        │                            │   kz-ext publish    │
   │   (deploy onto      │                            │   (push to App      │
   │    your dev cluster)│                            │    Garden catalog)  │
   └──────────┬──────────┘                            └──────────┬──────────┘
              │                                                  │
              ▼                                                  ▼
   ┌─────────────────────┐                            ┌─────────────────────┐
   │  Runtime CR model   │                            │  App Garden catalog │
   │  (Kubernetes CRD,   │                            │  (R2-backed JSON,   │
   │   live in cluster)  │                            │   per-tenant view)  │
   └─────────────────────┘                            └─────────────────────┘
```

| Surface | Owned by | Lives where | When you see it |
| --- | --- | --- | --- |
| Runtime CR | `kz-ext dev` | Kubernetes (CRD: `kamiwazaextensions.extensions.kamiwaza.io`) | Right now, on the cluster you targeted |
| App Garden | `kz-ext publish` | Object storage (R2 / S3-compatible) | Anyone in your tenant browsing the catalog |

`kz-ext dev` does **not** publish to the App Garden. `kz-ext publish` does
**not** deploy to a cluster. Both are intentional separations — one is
"my work-in-progress on my cluster," the other is "shareable artifact for
the org."

## `kz-ext create` — scaffolding

The empty-cwd convention is preserved for the historical workflow
(`mkdir foo && cd foo && kz-ext create --name foo`). When run from a
**non-empty** directory, the CLI now creates `./<name>/` and scaffolds
into it — no need to pre-create the directory yourself.

```sh
# Both of these work:
mkdir my-tool && cd my-tool && kz-ext create --type tool --name my-tool
# or, from any workspace root:
kz-ext create --type tool --name my-tool   # creates ./tool-my-tool/
```

The auto-prefix convention (`tool-` and `service-`) still applies.

### Generated app runtime

App scaffolds build two standalone Next.js artifacts into one production
image: a native port-routed build and a sentinel-prefixed path-routed build.
At container start, the runtime selects the native artifact or copies and
byte-relocates the indexed path artifact to `KAMIWAZA_APP_PATH` under `/tmp`.
It does not run `next build` during deployment startup.

The scaffold owns this contract:

- `frontend/Dockerfile` builds and packages both artifacts.
- `withKamiwazaAppGarden()` owns `basePath` and `assetPrefix`; do not set them
  directly.
- `next` is pinned to the exact version validated by the relocation canary.
- `KAMIWAZA_ROUTING_MODE` and `KAMIWAZA_APP_PATH` are runtime inputs, not
  public build variables.
- `NEXT_PUBLIC_APP_BASE_PATH` is legacy and must not be reintroduced.

Use `kz-ext update` to reconcile existing SDK-native extensions with a newer
scaffold before adopting this runtime.

For the full contract and migration guidance, see:

- [Path-based routing and dual artifacts](./runtime-path/path-based-routing-cheatsheet.md)
- [Auth integration](./auth-integration-guide.md)
- [Legacy-to-canonical import map](./auth-runtime-migration.md)
- [Logout flow](./logout-flow.md)

## `kz-ext dev local` — fast local iteration

`kz-ext dev local` runs the scaffolded `docker-compose.yml` against
your machine's Docker daemon — no cluster, no Kubernetes. It is the
quickest feedback loop while iterating.

By default the extension runs in standalone mode (`KAMIWAZA_USE_AUTH=false`):
all calls into the platform are anonymous. That's enough for iterating on
UI and pure-extension logic. When you need to exercise the real auth path
— forwarded-bearer model client, identity middleware, role checks —
add `--auth`:

```sh
kz-ext login --url https://kamiwaza.test --no-verify-ssl   # one-time per machine
kz-ext dev local --auth
```

`--auth` bridges your active `kz-ext login` connection's bearer into the
container, so the extension's identity / proxy / model-client paths see
the same `Authorization` and `x-user-id` envelope they get when the
extension is deployed behind the platform gateway. This is the **real
auth path** — no synthetic dev users — so any mismatch you'd hit in
production will fail here too. If no usable connection exists, `--auth`
fails loudly (e.g. `no active Kamiwaza connection — run \`kz-ext login\`
first`, exit code 2) instead of falling back to anonymous.

> **`--auth` is `app`-only in v1.** The bridge mechanism is the Next.js
> middleware shipped with the app template; `service`/`tool` extensions
> have no equivalent Python-side bridge yet. Running `--auth` against a
> non-`app` extension fails fast with a clear message — see the
> [CLI reference](./cli-reference/dev-local.md#fail-loud-behavior) for
> the full fail-loud table.

`--auth` also injects Docker host-gateway routing so containers can
reach loopback-bound Kamiwaza URLs on your host (`https://kamiwaza.test`,
`http://localhost:8000`, etc.) without extra plumbing. Bare loopbacks
are rewritten to `host.docker.internal`; named hostnames keep their
original name (so TLS SNI matches your local cert) and get an
`extra_hosts: <name>:host-gateway` entry in the compose overlay.

> **Frontend hot-reload:** `kz-ext dev local` selects the scaffold's `dev`
> image target and runs `next dev`; frontend source mounts therefore
> hot-reload. Production `kz-ext dev` and `kz-ext publish` builds still use
> the dual prebuilt-artifact runner described above. Backend changes
> hot-reload via `uvicorn --reload`.

> **`--auth` security note:** the bearer is passed to the container as
> `KAMIWAZA_BEARER_TOKEN` (visible to anything that can run `docker
> inspect` on your host). That's acceptable for local dev where you
> trust your own machine. The bearer is read once at start time — if
> you rotate the token via `kz-ext login` mid-session, restart
> `kz-ext dev local --auth` to pick up the new one.

> **Existing extensions:** the bridge depends on a Next.js middleware
> that the latest app template includes by default. If you scaffolded
> your extension before this landed, add this one-liner:
>
> ```ts
> // frontend/src/middleware.ts
> import type { NextRequest } from "next/server";
> import { createLocalDevAuthMiddleware } from "@kamiwaza-ai/extensions-lib/local-dev-auth";
>
> const localDevAuth = createLocalDevAuthMiddleware();
> export function middleware(request: NextRequest) { return localDevAuth(request); }
> export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
> ```
>
> The middleware is a pass-through whenever the bridge env var is unset
> (i.e. always in production), so it's safe to commit.

## `kz-ext dev` — deploy onto a cluster

Targets the cluster your active `kz-ext login` connection points at —
**not** your `kubectl` context. Builds your images, pushes them to the
resolved dev registry, then creates the CR through the platform API.
The deployed name is `<slug>-dev-<hash6>`, where `<slug>` is your
extension name as a DNS-1123 label and `<hash6>` is derived from your
user id, so two people on the same commit do not collide and your own
name stays stable across runs; the CLI prints it on success / timeout /
failure (P9, M1).

> **Where `kubectl` still matters.** Only for registry discovery, and
> only when your connection is local. For a loopback or explicit dev
> hostname, `kz-ext` reads the extension registry from the cluster's
> `core-config` ConfigMap via `kubectl` — so a wrong kube context can
> still give you a wrong registry. For any non-local connection it
> skips that deliberately and derives `registry.<connection-hostname>`
> instead, because your kube context may point at an unrelated cluster
> (ENG-5719). `KAMIWAZA_REGISTRY` overrides both.

The first run on a new cluster: confirm `kz-ext doctor` is green
(`cluster_extension_readiness` probe added in M1). A red doctor means
the cluster's `extension-operator` is not installed or not the version
the CLI expects — not an extension bug.

## `kz-ext publish` — App Garden catalog

Builds release-tagged images and writes a manifest into the catalog
bucket configured by your publish profile. Use `--revision <git-sha>`
(M1, ENG-3884) so re-runs from CI are idempotent.

```sh
kz-ext config publish-profile list           # subcommand-style
kz-ext config publish-profile --list         # legacy flag — still works
kz-ext publish --profile internal-develop --revision $(git rev-parse --short HEAD)
```

Publishing does not deploy. After `kz-ext publish` succeeds, anyone in
your tenant with App Garden access can install your extension from the
catalog — that install is what creates the runtime CR on their cluster.

## `kz-ext doctor` — pre-flight checks

Runs the bundled diagnostic checks (Python version, Docker, Compose,
cluster readiness, runtime-lib compatibility). Out-of-range runtime-lib
versions warn (per `kamiwaza_extensions/compatibility.json`, ENG-3897),
they don't fail — so you can choose to upgrade later.

## AI assistance for extension authors

When working in an extension repository with an AI assistant (Claude
Code, Cursor, Copilot), point the agent at `.ai/extensions/` if it
exists in your scaffold (or at this guide). The `.ai/` directory at
the SDK repo root is for SDK contributors — it has different
conventions and is not the right context for extension work. Keep
extension-specific AI context inside your extension repo.
