/**
 * Local-dev auth bridge middleware (ENG-4318).
 *
 * When `kz-ext dev local --auth` runs an extension, the Python runner sets
 * three env vars on the container:
 *
 *   KZ_EXT_DEV_LOCAL_AUTH=1
 *   KAMIWAZA_BEARER_TOKEN=<jwt>
 *   KAMIWAZA_DEV_WORKROOM_ID=<workroom-id>   (optional — defaults to JWT sub)
 *
 * This module exports a Next.js middleware factory that, when those env
 * vars are present, synthesizes the platform's forwarded-auth envelope
 * headers (`x-user-id`, `x-user-email`, `x-user-name`, `x-user-roles`,
 * `x-workroom-id`, `authorization`) from the bearer's JWT claims so the
 * rest of the extension code (proxy, identity extractor, session router,
 * AuthGuard) sees the same input shape it gets in production.
 *
 * `x-workroom-id` is required by the strict `extract_identity()` path used
 * by `create_session_router()` and `require_auth()` under
 * `KAMIWAZA_USE_AUTH=true`. Without it, every protected route 401s and
 * `/session` reports logged-out. See PR #87 review for details.
 *
 * Fail-closed semantics:
 *   - Gate env unset → pass-through (production behaviour).
 *   - Gate set but token unset → warn + pass-through (NOT a synthesized
 *     fake identity — that would be the "fuzzy identity" the D210 PRD-lite
 *     calls out as a risk).
 *   - Gate set, token present but unparseable → warn + pass-through.
 *   - Inbound request already has an authorization header → pass-through
 *     (defense in depth: even if the gate is accidentally enabled in a
 *     non-dev environment, real platform identity wins).
 */

import { NextResponse, type NextRequest } from "next/server";

/**
 * Compatible with Next.js's `middleware` export shape: a single-argument
 * function that returns a `NextResponse`. Distinct from `NextMiddleware`
 * (which is the two-argument form Next.js uses internally) — we expose
 * the single-argument shape so users can write
 * `export function middleware(req) { return localDevAuth(req); }` without
 * having to also forward the `NextFetchEvent`.
 */
export type LocalDevAuthMiddleware = (request: NextRequest) => NextResponse;

const GATE_ENV = "KZ_EXT_DEV_LOCAL_AUTH";
const TOKEN_ENV = "KAMIWAZA_BEARER_TOKEN";
const WORKROOM_ENV = "KAMIWAZA_DEV_WORKROOM_ID";

interface JwtClaims {
    sub?: string;
    email?: string;
    name?: string;
    // Keycloak-shape (the platform's current IdP)
    realm_access?: { roles?: string[] };
    // Top-level fallback for IdPs that don't use the Keycloak envelope
    // (Auth0, Okta with custom mapping, raw PAT-as-JWT issuers).
    roles?: string[];
}

function base64UrlDecode(input: string): string | null {
    try {
        const padded = input + "=".repeat((4 - (input.length % 4)) % 4);
        const standard = padded.replace(/-/g, "+").replace(/_/g, "/");
        // atob is universally available (Edge runtime, Node ≥18, browsers).
        return atob(standard);
    } catch {
        return null;
    }
}

/**
 * Decode a JWT payload without verifying the signature. The platform
 * verifies the bearer when it's actually used; we only read claims to
 * synthesize headers.
 */
function decodeJwt(token: string): JwtClaims | null {
    if (!token) return null;
    const parts = token.split(".");
    if (parts.length < 3) return null; // header.payload.signature
    const payloadStr = base64UrlDecode(parts[1]);
    if (payloadStr === null) return null;
    let parsed: unknown;
    try {
        parsed = JSON.parse(payloadStr);
    } catch {
        return null;
    }
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        return null;
    }
    return parsed as JwtClaims;
}

/**
 * One-time warning emitter. Round-2 review High #9 — the original
 * implementation re-read the env on every request and re-emitted the same
 * warning, flooding the log on a Next.js page with N parallel chunk
 * fetches. Throttle to once-per-process so the developer sees a single
 * line instead of N copies.
 */
const _warned = new Set<string>();
function warnOnce(key: string, message: string): void {
    if (_warned.has(key)) return;
    _warned.add(key);
    console.warn(message);
}

// Exported only for tests so the warn-once state can be reset between
// fixtures. Don't call this from production code.
export function _resetWarnOnceState(): void {
    _warned.clear();
}

/**
 * Resolved bridge configuration captured once at factory creation.
 *
 * Round-2 review High #12 — the original implementation read process.env
 * on every request, which is needlessly expensive and inconsistent with
 * the docs ("the bearer is read once at start time"). Resolve once at
 * factory creation and reuse the resolved view per request.
 */
interface ResolvedBridgeConfig {
    enabled: boolean;
    token: string | null;
    workroomOverride: string | null;
}

function resolveBridgeConfig(): ResolvedBridgeConfig {
    const enabled = process.env[GATE_ENV] === "1";
    const token = process.env[TOKEN_ENV] || null;
    const workroomRaw = process.env[WORKROOM_ENV]?.trim();
    return {
        enabled,
        token,
        workroomOverride: workroomRaw && workroomRaw.length > 0 ? workroomRaw : null,
    };
}

function rolesFromClaims(claims: JwtClaims): string[] {
    // Prefer Keycloak-shape `realm_access.roles` (matches the platform's
    // current IdP); fall back to a top-level `roles` array for non-Keycloak
    // tokens. Filter to strings to be defensive against malformed claims.
    const candidate = claims.realm_access?.roles ?? claims.roles;
    if (!Array.isArray(candidate)) return [];
    return candidate.filter((r) => typeof r === "string" && r.length > 0);
}

/**
 * Pure header transformation — exported for unit testing without a NextRequest.
 *
 * Returns the *same* Headers instance when no synthesis happens (gate unset,
 * token unset, decode failure, or already-authenticated request). Returns a
 * new Headers instance with the bridged envelope when synthesis applies.
 *
 * The optional `config` parameter is for tests; production callers go
 * through ``createLocalDevAuthMiddleware`` which captures the env once
 * at factory creation time.
 */
export function _buildBridgedHeaders(
    incoming: Headers,
    config: ResolvedBridgeConfig = resolveBridgeConfig(),
): Headers {
    if (!config.enabled) return incoming;

    // Defense-in-depth: if the inbound request is already authenticated,
    // don't override it. Real platform identity always wins.
    if (incoming.get("authorization")) return incoming;

    if (!config.token) {
        warnOnce(
            "missing-token",
            `${GATE_ENV}=1 but ${TOKEN_ENV} is unset — local-dev bridge inactive. ` +
            "Run `kz-ext login` and restart `kz-ext dev local --auth`.",
        );
        return incoming;
    }

    const claims = decodeJwt(config.token);
    if (!claims || !claims.sub) {
        warnOnce(
            "undecodable-token",
            `${TOKEN_ENV} could not be decoded as a JWT with a 'sub' claim — ` +
            "local-dev bridge inactive.",
        );
        return incoming;
    }

    const out = new Headers(incoming);
    out.set("authorization", `Bearer ${config.token}`);
    out.set("x-user-id", claims.sub);
    // x-workroom-id is required by strict extract_identity() under
    // KAMIWAZA_USE_AUTH=true (session.py:131, identity.py:149). Without
    // it, every protected route 401s. Honor an explicit override from
    // KAMIWAZA_DEV_WORKROOM_ID; otherwise fall back to the JWT sub so the
    // strict identity path succeeds and the workroom_id is stable
    // per-developer. The synthesized workroom_id is only consumed by
    // the extension's local authz path — it's never sent to the
    // platform (which only sees the bearer).
    out.set("x-workroom-id", config.workroomOverride || claims.sub);
    if (typeof claims.email === "string" && claims.email) {
        out.set("x-user-email", claims.email);
    }
    if (typeof claims.name === "string" && claims.name) {
        out.set("x-user-name", claims.name);
    }
    const roles = rolesFromClaims(claims);
    if (roles.length > 0) {
        out.set("x-user-roles", roles.join(","));
    }
    return out;
}

/**
 * Returns a Next.js middleware that bridges the developer's identity from
 * `kz-ext login` into the running extension when `kz-ext dev local --auth`
 * has set the bridge env vars on the container.
 *
 * Wire it into your extension's `app/middleware.ts`:
 *
 * ```ts
 * import { createLocalDevAuthMiddleware } from "@kamiwaza-ai/extensions-lib/server";
 *
 * const localDevAuth = createLocalDevAuthMiddleware();
 * export function middleware(request) { return localDevAuth(request); }
 *
 * export const config = {
 *     matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
 * };
 * ```
 *
 * Safe to wire unconditionally — the middleware is a pass-through when the
 * gate env var is not set.
 */
export function createLocalDevAuthMiddleware(): LocalDevAuthMiddleware {
    // Capture the env once at factory creation. Per the docs, the bridge
    // bearer is read at start time; restart `kz-ext dev local --auth` to
    // pick up a new token.
    const config = resolveBridgeConfig();
    return (req: NextRequest): NextResponse => {
        const bridged = _buildBridgedHeaders(req.headers, config);
        if (bridged === req.headers) return NextResponse.next();
        return NextResponse.next({ request: { headers: bridged } });
    };
}

// Exported for testing only.
export { decodeJwt as _decodeJwt };
