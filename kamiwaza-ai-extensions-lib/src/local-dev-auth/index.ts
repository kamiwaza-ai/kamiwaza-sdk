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

import { ENVELOPE_AUTH_HEADERS } from "../_shared/envelopeHeaders";

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

/**
 * Forwarded-auth envelope headers that the platform gateway owns in
 * production. Under the local-dev bridge we MUST clear all of these
 * before injecting our synthesized values — otherwise a client-supplied
 * spoof (e.g. `x-user-system-high: 1`) would slip through unchanged and
 * make local auth tests pass for permissions the user doesn't actually
 * have. Sourced from the shared `ENVELOPE_AUTH_HEADERS` constant so
 * `proxy.ts` (forward) and this file (clear) cannot drift —
 * round-10 review caught this maintainability gap.
 */
const FORWARDED_AUTH_HEADERS = ENVELOPE_AUTH_HEADERS;

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
        // atob() returns a Latin-1 binary string. JWT payloads are
        // canonically UTF-8 (RFC 7519 §2 + §3), so any claim with
        // non-ASCII bytes (most often `name`, sometimes `email`) would
        // come back as mojibake if we passed the atob output straight
        // to JSON.parse. Round-4 review (codex) caught this — decode
        // the raw bytes as UTF-8 before returning the string.
        const binary = atob(standard);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
        }
        return new TextDecoder("utf-8").decode(bytes);
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

    // PR #87 round-6 review (codex P2) — sanitize ALL forwarded-auth
    // envelope headers before bridging. Starting from `new Headers(incoming)`
    // and only `set()`-ing a subset would preserve client-supplied values
    // for headers we don't bridge (e.g. a request with no `authorization`
    // but with `x-user-system-high: 1` or `x-user-workroom-role: admin`
    // would forward those spoofed values to the backend, making local
    // auth tests pass in ways production wouldn't — in production the
    // platform gateway owns the entire envelope). Clear every header in
    // FORWARDED_AUTH_HEADERS first, then set only the synthesized values.
    //
    // Round-13 review (codex P2) — the round-6 fix only ran on the
    // no-inbound-Authorization path. The original early-return on
    // inbound ``Authorization`` preserved EVERY envelope header
    // including spoofs, opening a privilege-escalation bypass:
    // ``Authorization: anything`` + ``x-user-id: admin`` +
    // ``x-user-system-high: 1`` reached the backend untouched. The
    // sanitization now runs unconditionally on every gate-on path —
    // when the bridge is active there's no platform gateway, so
    // spoofs never have a legitimate source.
    const inboundAuthorization = incoming.get("authorization");
    const out = new Headers(incoming);
    for (const header of FORWARDED_AUTH_HEADERS) {
        out.delete(header);
    }

    // Defense-in-depth: if the inbound request was already authenticated,
    // honor that ``Authorization`` rather than overriding with the
    // bridge's synthesized one (covers the "bridge accidentally
    // enabled in production" leak case — real platform identity wins).
    // The envelope above was still cleared so spoofed envelope fields
    // can't survive alongside the inbound bearer.
    if (inboundAuthorization) {
        out.set("authorization", inboundAuthorization);
        return out;
    }

    if (!config.token) {
        warnOnce(
            "missing-token",
            `${GATE_ENV}=1 but ${TOKEN_ENV} is unset — local-dev bridge inactive. ` +
            "Run `kz-ext login` and restart `kz-ext dev local --auth`.",
        );
        // Return ``out`` (envelope cleared) rather than ``incoming`` —
        // the bridge is misconfigured, but spoofs still must not pass
        // through under --auth (no platform gateway here).
        return out;
    }

    const claims = decodeJwt(config.token);
    if (!claims || !claims.sub) {
        warnOnce(
            "undecodable-token",
            `${TOKEN_ENV} could not be decoded as a JWT with a 'sub' claim — ` +
            "local-dev bridge inactive.",
        );
        return out;
    }


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
 * import { createLocalDevAuthMiddleware } from "@kamiwaza-ai/extensions-lib/local-dev-auth";
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
    // pick up a new token. Round-10 review (Comprehensive H): under
    // `next dev`'s HMR the factory may not re-instantiate when only the
    // bearer rotates, so a stale token can silently persist. Log the
    // resolved bridge user_id at creation so a developer can spot it
    // in the dev-server console after `kz-ext login --use other`.
    const config = resolveBridgeConfig();
    if (config.enabled && config.token) {
        const claims = decodeJwt(config.token);
        const sub = typeof claims?.sub === "string" ? claims.sub : "(no sub)";
        // eslint-disable-next-line no-console
        console.info(
            `[local-dev-auth] bridge active for user_id=${sub}` +
                ` (token captured at factory creation — restart` +
                ` 'kz-ext dev local --auth' to refresh)`,
        );
    }
    return (req: NextRequest): NextResponse => {
        const bridged = _buildBridgedHeaders(req.headers, config);
        if (bridged === req.headers) return NextResponse.next();
        return NextResponse.next({ request: { headers: bridged } });
    };
}

// Exported for testing only.
export { decodeJwt as _decodeJwt };
