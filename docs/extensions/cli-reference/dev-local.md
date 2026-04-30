# `kz-ext dev local`

Run the scaffolded extension against your local Docker daemon.

## Usage

```sh
kz-ext dev local [--detach] [--sdk-repo <path>] [--auth]
```

| Flag | Behavior |
| --- | --- |
| (no flag) | Foreground `docker compose up`. Standalone mode: `KAMIWAZA_USE_AUTH=false`; calls into the platform are anonymous. |
| `--detach` / `-d` | Run in background. Re-prints access URLs once Docker has published the host ports. |
| `--sdk-repo <path>` | Override the runtime-lib install with a local `kamiwaza-sdk` checkout (mounts the local source + rebuilds the TS lib). |
| `--auth` | Bridge the developer's identity from the active `kz-ext login` connection into the running extension, and inject Docker host-gateway routing for loopback-bound Kamiwaza URLs. |

## `--auth` behavior

`--auth` opts the local-dev session into the **real auth path**. The runner:

1. Reads the active `kz-ext login` connection (URL, bearer, `verify_ssl`).
2. Validates the bearer locally — JWT `exp` decode (no signature check, just expiry).
3. Sets these env vars on every container service:
   - `KZ_EXT_DEV_LOCAL_AUTH=1` — the gate the bridge middleware reads.
   - `KAMIWAZA_BEARER_TOKEN=<bearer>` — the developer's real token.
   - `KAMIWAZA_USE_AUTH=true` — flips the extension into auth-on mode.
   - `KAMIWAZA_DEV_WORKROOM_ID=<id>` — *optional override*. If unset, the bridge synthesizes `x-workroom-id` from the JWT `sub` so the strict identity path succeeds. Set this on your shell before running `kz-ext dev local --auth` if you want to test against a specific workroom.
4. Rewrites bare loopback URLs (`localhost`, `127.0.0.1`, `::1`) in the env overlay to `host.docker.internal` so containers can reach them.
5. For named loopback hostnames (`*.test`, `*.local`, or any host that fails DNS resolution from your machine), generates a compose overlay that adds `extra_hosts: <name>:host-gateway` to every service. The hostname is preserved unchanged so TLS SNI keeps matching your host certificate.
6. **Always** adds `extra_hosts: host.docker.internal:host-gateway` to the same overlay. Docker Desktop resolves `host.docker.internal` implicitly, but plain Linux Docker Engine does not — without this alias the bare-loopback URL rewrite (step 4) fails on Linux with name-resolution errors. Harmless on Docker Desktop where it's already aliased.

The Next.js middleware shipped with the app template (and exposed as `createLocalDevAuthMiddleware()` from `@kamiwaza-ai/extensions-lib/local-dev-auth`) reads `KZ_EXT_DEV_LOCAL_AUTH` + `KAMIWAZA_BEARER_TOKEN` and synthesizes the platform's forwarded-auth envelope (`Authorization`, `x-user-id`, `x-user-email`, `x-user-name`, `x-user-roles`, `x-workroom-id`) on every inbound request. The rest of the extension code (proxy, identity extractor, session router, `AuthGuard`) sees the same shape it does in production.

`x-workroom-id` is required by `extract_identity()`'s strict path (used by `create_session_router()` and `require_auth()` under `KAMIWAZA_USE_AUTH=true`). The bridge defaults it to the JWT `sub` so the strict path succeeds; set `KAMIWAZA_DEV_WORKROOM_ID` to override with a specific workroom for testing.

### Fail-loud behavior

`--auth` fails fast — no silent fallback to anonymous, no synthetic dev users — when the bridge can't be set up:

| Condition | Message | Exit |
| --- | --- | --- |
| Extension type is not `app` | `--auth is only supported for \`app\`-type extensions; this extension type is \`<type>\`...` | 2 |
| No active `kz-ext login` connection | `no active Kamiwaza connection — run \`kz-ext login\` first` | 2 |
| Active connection has empty bearer | `active connection '<name>' has no stored bearer token — run \`kz-ext login\` again` | 2 |
| JWT `exp` is in the past | `bearer token expired at <ISO> — run \`kz-ext login\` again` | 2 |
| Bearer is not a JWT (no `sub` claim) | `active connection '<name>' bearer is not a JWT with a usable \`sub\` claim — \`kz-ext dev local --auth\` requires an interactive login (try \`kz-ext login\` without \`--api-key\`)` | 2 |

**`--auth` is `app`-only in v1:** the bridge mechanism is the Next.js middleware shipped with the app template (`kz-ext create --type app`). For `service`-type and `tool`-type extensions there's no Next.js layer to inject envelope headers, so `--auth` would set `KAMIWAZA_USE_AUTH=true` against a backend that has no way to receive forwarded-auth headers — every request would 401. Run those without `--auth` for v1; a Python-side bridge for service/tool is tracked as a follow-up.

A JWT with no `exp` claim is accepted (the platform validates the bearer at request time and the extension surfaces the platform's 401 directly). However, the bearer **must be a JWT with a usable `sub` claim** — opaque PATs / API keys created via `kz-ext login --api-key` cannot drive the bridge because the middleware needs `sub` to synthesize `x-user-id`. Use an interactive `kz-ext login` for `--auth`-based local dev.

### Security notes

- The bearer is in container env (visible to `docker inspect` on your host). Acceptable for local dev where you trust your own machine.
- The middleware **never overrides** an inbound `Authorization` header — defense in depth, so even if the gate env accidentally bleeds into a non-dev environment, real platform identity wins.
- `KZ_EXT_DEV_LOCAL_AUTH=1` only takes effect when set explicitly. The default (unset) is a hard pass-through with no synthesized identity.
- The bearer is read once at start time. Restart `kz-ext dev local --auth` after rotating the token via `kz-ext login`.

### Adoption for existing extensions

The middleware is shipped with the app template, so new extensions get the bridge for free. Existing extensions need a one-line `frontend/src/middleware.ts`:

```ts
import type { NextRequest } from "next/server";
import { createLocalDevAuthMiddleware } from "@kamiwaza-ai/extensions-lib/local-dev-auth";

const localDevAuth = createLocalDevAuthMiddleware();
export function middleware(request: NextRequest) { return localDevAuth(request); }
export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
```

The middleware is a pass-through whenever the gate env var is unset (i.e. always in production), so it's safe to commit.

## See also

- `kz-ext login` — establish the connection that `--auth` reads
- [Developer guide — `kz-ext dev local`](../developer-guide.md#kz-ext-dev-local--fast-local-iteration)
