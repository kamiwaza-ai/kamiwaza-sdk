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
4. Rewrites bare loopback URLs (`localhost`, `127.0.0.1`, `::1`) in the env overlay to `host.docker.internal` so containers can reach them.
5. For named loopback hostnames (`*.test`, `*.local`, or any host that fails DNS resolution from your machine), generates a compose overlay that adds `extra_hosts: <name>:host-gateway` to every service. The hostname is preserved unchanged so TLS SNI keeps matching your host certificate.

The Next.js middleware shipped with the app template (and exposed as `createLocalDevAuthMiddleware()` from `@kamiwaza-ai/extensions-lib/server`) reads `KZ_EXT_DEV_LOCAL_AUTH` + `KAMIWAZA_BEARER_TOKEN` and synthesizes the platform's forwarded-auth envelope (`Authorization`, `x-user-id`, `x-user-email`, `x-user-name`, `x-user-roles`) on every inbound request. The rest of the extension code (proxy, identity extractor, session router, `AuthGuard`) sees the same shape it does in production.

### Fail-loud behavior

`--auth` fails fast — no silent fallback to anonymous, no synthetic dev users — when the bridge can't be set up:

| Condition | Message | Exit |
| --- | --- | --- |
| No active `kz-ext login` connection | `no active Kamiwaza connection — run \`kz-ext login\` first` | 2 |
| Active connection has empty bearer | `active connection '<name>' has no stored bearer token — run \`kz-ext login\` again` | 2 |
| JWT `exp` is in the past | `bearer token expired at <ISO> — run \`kz-ext login\` again` | 2 |

A bearer with no `exp` claim (e.g. a long-lived PAT) is accepted; the platform will reject it at request time if it's invalid (the extension surfaces the platform's 401 directly).

### Security notes

- The bearer is in container env (visible to `docker inspect` on your host). Acceptable for local dev where you trust your own machine.
- The middleware **never overrides** an inbound `Authorization` header — defense in depth, so even if the gate env accidentally bleeds into a non-dev environment, real platform identity wins.
- `KZ_EXT_DEV_LOCAL_AUTH=1` only takes effect when set explicitly. The default (unset) is a hard pass-through with no synthesized identity.
- The bearer is read once at start time. Restart `kz-ext dev local --auth` after rotating the token via `kz-ext login`.

### Adoption for existing extensions

The middleware is shipped with the app template, so new extensions get the bridge for free. Existing extensions need a one-line `frontend/src/middleware.ts`:

```ts
import type { NextRequest } from "next/server";
import { createLocalDevAuthMiddleware } from "@kamiwaza-ai/extensions-lib/server";

const localDevAuth = createLocalDevAuthMiddleware();
export function middleware(request: NextRequest) { return localDevAuth(request); }
export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
```

The middleware is a pass-through whenever the gate env var is unset (i.e. always in production), so it's safe to commit.

## See also

- `kz-ext login` — establish the connection that `--auth` reads
- [Developer guide — `kz-ext dev local`](../developer-guide.md#kz-ext-dev-local--fast-local-iteration)
