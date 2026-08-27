# Auth Integration Guide

Use the canonical extension runtime libraries for all new and migrated
extensions:

- TypeScript: `@kamiwaza-ai/extensions-lib@0.5.x`
- Python: `kamiwaza-extensions-lib==0.5.*`

The deprecated `kamiwaza-extensions-template` repository shipped
`@kamiwaza/auth@0.2.0` and `kamiwaza_auth`. Those packages retain their
cookie-era behavior for existing extensions, but they are not compatibility
aliases for the canonical runtime and must not be used in new code. See
[Auth Runtime Migration](./auth-runtime-migration.md) for the import map and
staged migration policy.

> **Release status:** version 0.5 must be published before package-manager
> installs and clean lockfile regeneration can succeed. Before publication,
> use an approved 0.5 wheel/tarball from the SDK release candidate.

## How authentication works

The platform gateway authenticates browser requests before they reach an
extension and injects the forwarded-auth envelope (`x-user-id`,
`x-user-email`, `authorization`, and related headers). Extension code consumes
that envelope through the canonical libraries:

1. `SessionProvider` fetches the extension backend's `/session` endpoint.
2. `AuthGuard` renders only after the session is resolved.
3. `createProxyHandlers` forwards the approved auth envelope to the backend.
4. `require_auth` protects FastAPI routes.
5. `createLocalDevAuthMiddleware` synthesizes the same envelope only for
   `kz-ext dev local --auth`.

The middleware is a local-development bridge, not the production
authentication boundary. In production, the platform gateway remains the
boundary.

## Install

After 0.5 is published:

```bash
# Frontend
npm install '@kamiwaza-ai/extensions-lib@^0.5.0'

# Backend
pip install 'kamiwaza-extensions-lib==0.5.*'
```

For an unpublished release candidate, substitute the approved local `.tgz`
and `.whl` paths. Regenerate and commit lockfiles once the packages are
published.

## FastAPI backend

Add the session routes and protect endpoints with `require_auth`:

```python
from fastapi import Depends, FastAPI, Request
from kamiwaza_extensions_lib import (
    Identity,
    create_session_router,
    get_identity,
    require_auth,
)

app = FastAPI()
app.include_router(create_session_router())


@app.get("/api/private")
async def private(identity: Identity = Depends(require_auth)):
    return {"email": identity.email, "roles": identity.roles}


@app.get("/api/public")
async def public(request: Request):
    identity = await get_identity(request)
    return {"authenticated": identity.is_authenticated}
```

`create_session_router()` installs `/session`, `/auth/login-url`, and
`/auth/logout`. Keep those routes unprefixed in application code. The
generated Next proxy strips the public deployment prefix before forwarding;
the ASGI launcher supplies the backend's external `root_path` context:

```dockerfile
CMD ["python", "-m", "kamiwaza_extensions_lib.asgi", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000"]
```

## Next.js frontend

### Base-path configuration

Wrap the app's Next configuration with the SDK-owned path contract:

```javascript
const { withKamiwazaAppGarden } = require(
  "@kamiwaza-ai/extensions-lib/next-config",
);

module.exports = withKamiwazaAppGarden({
  output: "standalone",
});
```

Do not set `basePath` or `assetPrefix` yourself. The wrapper selects the
prebuilt port/path variant and rejects unsupported combinations.

### Local-development auth bridge

Create `middleware.ts`:

```typescript
import type { NextRequest } from "next/server";
import { createLocalDevAuthMiddleware } from
  "@kamiwaza-ai/extensions-lib/local-dev-auth";

const localDevAuth = createLocalDevAuthMiddleware();

export function middleware(request: NextRequest) {
  return localDevAuth(request);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
```

When `KZ_EXT_DEV_LOCAL_AUTH` is unset, this middleware passes requests
through. Do not use the legacy `createAuthMiddleware`; it implements a
different cookie-based path contract.

### Runtime bootstrap and session provider

Install the runtime bootstrap in the root layout before hydration:

```tsx
import { KamiwazaRuntimeBootstrap } from
  "@kamiwaza-ai/extensions-lib/runtime";
import { Providers } from "./providers";

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <KamiwazaRuntimeBootstrap />
      </head>
      <body><Providers>{children}</Providers></body>
    </html>
  );
}
```

Put the client provider in `app/providers.tsx`:

```tsx
"use client";

import { SessionProvider } from "@kamiwaza-ai/extensions-lib/client";

export function Providers({ children }: { children: React.ReactNode }) {
  return <SessionProvider>{children}</SessionProvider>;
}
```

`SessionProvider` derives the deployment prefix from the runtime bootstrap.
The `basePath` prop exists only as a deprecated escape hatch.

### Protect content and inspect the session

```tsx
"use client";

import {
  AuthGuard,
  useSession,
} from "@kamiwaza-ai/extensions-lib/client";

export function AccountPanel() {
  const { session, logout } = useSession();

  return (
    <AuthGuard fallback={<p>Loading session…</p>}>
      <p>{session?.email}</p>
      <button onClick={() => void logout()}>Log out</button>
    </AuthGuard>
  );
}
```

The canonical `AuthGuard` accepts `children` and an optional `fallback`; it
does not accept the legacy `publicRoutes` prop.

Guard protected pages or components, not the root layout. In auth-enabled
production, the platform gateway normally redirects an unauthenticated HTML
request under `/runtime/apps/<id>/...` to platform login before Next.js or
`AuthGuard` runs. An extension-owned `app/logged-out/page.tsx` is therefore
only a local/auth-disabled fallback unless the platform defines a narrowly
scoped public edge route; placing it outside `AuthGuard` does not make it
public at ingress.

### Proxy frontend API routes

The client calls `/session`, `/auth/login-url`, and `/auth/logout`, so expose
those three routes in addition to the application API catch-all.

For `app/session/route.ts`:

```typescript
import { createProxyHandlers } from
  "@kamiwaza-ai/extensions-lib/server";

const { GET } = createProxyHandlers({
  target: process.env.BACKEND_URL || "http://backend:8000",
  setCookiePaths: ["/session"],
});

export { GET };
```

Use the same target for `app/auth/login-url/route.ts` (export `GET`). For
`app/auth/logout/route.ts`, export `POST` and set
`setCookiePaths: ["/auth/logout"]` so the trusted logout response can clear
its cookie. Keep `Set-Cookie` disabled on all other proxy routes.

For `app/api/[...path]/route.ts`:

```typescript
import { createProxyHandlers } from
  "@kamiwaza-ai/extensions-lib/server";

const { DELETE, GET, PATCH, POST, PUT } = createProxyHandlers({
  target: process.env.BACKEND_URL || "http://backend:8000",
});

export { DELETE, GET, PATCH, POST, PUT };
```

The proxy strips the runtime deployment prefix and forwards the approved auth
and routing headers. `Set-Cookie` is denied by default; routes that intentionally
proxy a trusted session endpoint must opt in with `setCookiePaths`.

### Fetch paths and assets

```typescript
import {
  appAsset,
  appFetch,
  getAppPath,
} from "@kamiwaza-ai/extensions-lib/runtime";

const models = await appFetch("/api/models").then((response) => response.json());
const iconUrl = appAsset("/kmza-icon.png");
const socketPath = `${getAppPath()}/api/events`;
```

The legacy `@kamiwaza/auth` implementation of `apiFetch` is not a
compatibility alias for canonical `appFetch`. Migrate call sites explicitly.

## Local development

Use the scaffolded runner:

```bash
kz-ext dev local --auth
```

The runner supplies the bearer token and enables the local bridge. Do not
commit tokens or copy production cookies into local configuration.

Without `--auth`, the bridge is disabled and the backend's configured local
auth policy applies.

## Login and logout

`AuthGuard` asks the backend for `/auth/login-url` when an authenticated
session is unavailable. This supports local/auth-disabled operation and
client-side edge cases; it is not the primary production ingress flow. The
auth-enabled gateway redirects an unauthenticated top-level HTML request
before the app loads, and may return 401 to an unauthenticated fetch of the
same endpoint.

`useSession().logout()` posts to `/auth/logout`, prefers the platform
front-channel logout URL when supplied, and otherwise uses a safe redirect
under the current runtime path.

Applications should call `logout()` and let the canonical client own
navigation. An unguarded `app/logged-out/page.tsx` may serve as a
local/auth-disabled fallback, but auth-enabled production ingress can redirect
that request to platform login. See [Logout Flow](./logout-flow.md).

## Migration checklist

### Backend

- [ ] Depend on `kamiwaza-extensions-lib==0.5.*`.
- [ ] Replace `kamiwaza_auth` imports using the migration table.
- [ ] Include `create_session_router()`.
- [ ] Protect private routes with `Depends(require_auth)`.
- [ ] Launch through `python -m kamiwaza_extensions_lib.asgi`.
- [ ] Remove vendored legacy wheels after the canonical dependency resolves.

### Frontend

- [ ] Depend on `@kamiwaza-ai/extensions-lib@0.5.x`.
- [ ] Wrap `next.config.js` with
      `withKamiwazaAppGarden({ output: "standalone" })`.
- [ ] Install `<KamiwazaRuntimeBootstrap />` before hydration.
- [ ] Import session components from `/client`.
- [ ] If local/auth-disabled operation needs `app/logged-out/page.tsx`, keep
      it outside `AuthGuard`; do not treat that as a production ingress
      exemption.
- [ ] Import proxy handlers from `/server`.
- [ ] Proxy `/session`, `/auth/login-url`, and `/auth/logout`.
- [ ] Import local middleware from `/local-dev-auth`.
- [ ] Replace `apiFetch` and raw public-root paths with `/runtime` helpers.
- [ ] Remove vendored `@kamiwaza/auth` tarballs after migration.
- [ ] Regenerate and commit the lockfile after 0.5 is published.

## Troubleshooting

### Session requests use the wrong path

Confirm the root layout renders `KamiwazaRuntimeBootstrap` and that client
code uses the canonical `SessionProvider`. Do not add an `app-base-path`
cookie or `NEXT_PUBLIC_APP_BASE_PATH`.

### Local requests lack identity headers

Run `kz-ext dev local --auth`, confirm `KZ_EXT_DEV_LOCAL_AUTH=1` reaches the
frontend, and use `createLocalDevAuthMiddleware` from `/local-dev-auth`.

### Backend returns 401

Confirm the request passed through the canonical proxy, the platform gateway
injected the forwarded-auth envelope, and the backend route uses
`require_auth`. Avoid accepting user-supplied identity headers directly.

## Related documentation

- [Auth Runtime Migration](./auth-runtime-migration.md)
- [Logout Flow](./logout-flow.md)
- [Path-Based Routing Cheatsheet](./runtime-path/path-based-routing-cheatsheet.md)
