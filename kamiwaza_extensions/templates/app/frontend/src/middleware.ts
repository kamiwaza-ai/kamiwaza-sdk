/**
 * Next.js middleware for Kamiwaza extension apps.
 *
 * In production, the platform's auth gateway sits in front of the
 * extension and injects forwarded-auth envelope headers (`x-user-id`,
 * `x-user-email`, `authorization`, …) on every request. The extension's
 * proxy / identity / session code reads those headers directly.
 *
 * In `kz-ext dev local --auth` mode, there's no gateway in front of the
 * container, so `createLocalDevAuthMiddleware` synthesizes the same
 * envelope from the developer's bearer (passed in via the
 * KAMIWAZA_BEARER_TOKEN env var the runner sets) when
 * KZ_EXT_DEV_LOCAL_AUTH=1 is also set. When the gate env is unset (the
 * default in production and in `kz-ext dev local` without `--auth`), the
 * middleware is a pass-through.
 */

import type { NextRequest } from "next/server";
// Imported from the dedicated `/local-dev-auth` subpath so non-Next
// consumers of `@kamiwaza-ai/extensions-lib/server` don't pay for a
// next/server load they never use (PR #87 round-3 review).
import { createLocalDevAuthMiddleware } from "@kamiwaza-ai/extensions-lib/local-dev-auth";

const localDevAuth = createLocalDevAuthMiddleware();

export function middleware(request: NextRequest) {
    return localDevAuth(request);
}

export const config = {
    // Match every request except Next.js static assets and the favicon.
    matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
