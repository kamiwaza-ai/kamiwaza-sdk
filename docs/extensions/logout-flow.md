# Logout Flow

This document describes the canonical logout flow for App Garden extensions
using `@kamiwaza-ai/extensions-lib@0.5.x` and
`kamiwaza-extensions-lib==0.5.*`.

## Flow

1. UI code calls `logout()` from `useSession()`.
2. `SessionProvider` posts to `<runtime app path>/auth/logout`.
3. The extension backend's canonical session router proxies the logout to the
   Kamiwaza platform.
4. If the response contains a valid HTTP(S)
   `front_channel_logout_url`, the client navigates there to clear the
   platform/identity-provider session.
5. Otherwise, the client accepts a safe same-origin `redirect_url` or selects
   `<runtime app path>/logged-out` as its local fallback.
6. If the request or response cannot be used, the provider clears its local
   session state.

In auth-enabled production, every child of `/runtime/apps/<id>` is normally
covered by platform ForwardAuth. Once logout removes the platform session, a
top-level request for the extension's `/logged-out` fallback is intercepted
before Next.js and redirected to platform login. The extension-owned page is
renderable in local/auth-disabled operation; making it public in production
would require a separately reviewed, narrowly scoped ingress policy.

The runtime app path comes from `KamiwazaRuntimeBootstrap`; it does not come
from a cookie or `NEXT_PUBLIC_APP_BASE_PATH`.

## Backend

Install the canonical session routes:

```python
from fastapi import FastAPI
from kamiwaza_extensions_lib import create_session_router

app = FastAPI()
app.include_router(create_session_router())
```

This adds `/session`, `/auth/login-url`, and `/auth/logout`. Keep the routes
unprefixed in application code. The runtime routing contract supplies the
deployment prefix externally. The Next frontend must expose matching proxy
routes; for example, `app/auth/logout/route.ts` is:

```typescript
import { createProxyHandlers } from
  "@kamiwaza-ai/extensions-lib/server";

const { POST } = createProxyHandlers({
  target: process.env.BACKEND_URL || "http://backend:8000",
});

export { POST };
```

The auth integration guide lists the corresponding session and login routes.

## Frontend provider

Render the runtime bootstrap before hydration:

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
      <head><KamiwazaRuntimeBootstrap /></head>
      <body><Providers>{children}</Providers></body>
    </html>
  );
}
```

The provider uses the bootstrap-derived path:

```tsx
"use client";

import { SessionProvider } from "@kamiwaza-ai/extensions-lib/client";

export function Providers({ children }: { children: React.ReactNode }) {
  return <SessionProvider>{children}</SessionProvider>;
}
```

## Logout button

```tsx
"use client";

import { useSession } from "@kamiwaza-ai/extensions-lib/client";

export function LogoutButton() {
  const { logout } = useSession();

  return (
    <button type="button" onClick={() => void logout()}>
      Log out
    </button>
  );
}
```

The canonical `logout()` function owns the navigation. Applications should
not duplicate the front-channel URL validation or construct an unprefixed
`/logged-out` URL themselves.

If local/auth-disabled operation needs a fallback page, create
`app/logged-out/page.tsx` and keep it outside `AuthGuard`. The canonical guard
has no `publicRoutes` prop. This component boundary does not bypass production
ForwardAuth or make the route public at ingress.

## Security properties

- Cookies are sent to the extension backend with `credentials: "include"`.
- The frontend proxy denies `Set-Cookie` by default, including on the canonical
  session and logout routes. Custom cookie-minting integrations must opt in
  explicitly with `setCookiePaths` and trust their upstream's attributes.
- `front_channel_logout_url` may be cross-origin because the platform API and
  app can have different origins. The client rejects non-HTTP(S) schemes; its
  origin trust depends on the URL coming from the canonical extension backend,
  whose platform redirect target is validated against Core's allowed hosts.
- Ordinary `redirect_url` values must be relative or same-origin.
- A failed logout still clears the provider's local session state.

## Legacy package

`@kamiwaza/auth@0.2.0` and `kamiwaza_auth` are frozen legacy
implementations. Their cookie-era path discovery is not the 0.5 contract.
Migrate imports explicitly rather than assuming a re-export shim.

See [Auth Runtime Migration](./auth-runtime-migration.md) and
[Auth Integration Guide](./auth-integration-guide.md).
