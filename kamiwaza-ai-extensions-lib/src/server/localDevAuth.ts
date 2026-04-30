/**
 * Local-dev auth bridge middleware (ENG-4318).
 *
 * When `kz-ext dev local --auth` runs an extension, the Python runner sets
 * two env vars on the container:
 *
 *   KZ_EXT_DEV_LOCAL_AUTH=1
 *   KAMIWAZA_BEARER_TOKEN=<jwt>
 *
 * This module exports a Next.js middleware factory that, when those env
 * vars are present, synthesizes the platform's forwarded-auth envelope
 * headers (`x-user-id`, `x-user-email`, `x-user-name`, `x-user-roles`,
 * `authorization`) from the bearer's JWT claims so the rest of the
 * extension code (proxy, identity extractor, session router, AuthGuard)
 * sees the same input shape it gets in production.
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

interface JwtClaims {
    sub?: string;
    email?: string;
    name?: string;
    realm_access?: { roles?: string[] };
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
 * Pure header transformation — exported for unit testing without a NextRequest.
 *
 * Returns the *same* Headers instance when no synthesis happens (gate unset,
 * token unset, decode failure, or already-authenticated request). Returns a
 * new Headers instance with the bridged envelope when synthesis applies.
 */
export function _buildBridgedHeaders(incoming: Headers): Headers {
    if (process.env[GATE_ENV] !== "1") return incoming;

    // Defense-in-depth: if the inbound request is already authenticated,
    // don't override it. Real platform identity always wins.
    if (incoming.get("authorization")) return incoming;

    const token = process.env[TOKEN_ENV];
    if (!token) {
        console.warn(
            `${GATE_ENV}=1 but ${TOKEN_ENV} is unset — local-dev bridge inactive. ` +
            "Run `kz-ext login` and restart `kz-ext dev local --auth`.",
        );
        return incoming;
    }

    const claims = decodeJwt(token);
    if (!claims || !claims.sub) {
        console.warn(
            `${TOKEN_ENV} could not be decoded as a JWT with a 'sub' claim — ` +
            "local-dev bridge inactive.",
        );
        return incoming;
    }

    const out = new Headers(incoming);
    out.set("authorization", `Bearer ${token}`);
    out.set("x-user-id", claims.sub);
    if (typeof claims.email === "string" && claims.email) {
        out.set("x-user-email", claims.email);
    }
    if (typeof claims.name === "string" && claims.name) {
        out.set("x-user-name", claims.name);
    }
    const roles = claims.realm_access?.roles;
    if (Array.isArray(roles) && roles.length > 0) {
        out.set(
            "x-user-roles",
            roles.filter((r) => typeof r === "string").join(","),
        );
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
    return (req: NextRequest): NextResponse => {
        const bridged = _buildBridgedHeaders(req.headers);
        if (bridged === req.headers) return NextResponse.next();
        return NextResponse.next({ request: { headers: bridged } });
    };
}

// Exported for testing only.
export { decodeJwt as _decodeJwt };
