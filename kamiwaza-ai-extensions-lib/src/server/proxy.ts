import { ENVELOPE_AUTH_HEADERS } from "../_shared/envelopeHeaders";
import type { ProxyConfig } from "./types";

/** Headers to forward from the incoming request to the backend.
 *
 * The auth-bearing subset comes from the shared
 * ``ENVELOPE_AUTH_HEADERS`` constant — round-10 review caught that
 * the local-dev-auth bridge's clear-list and this forward-list were
 * hand-maintained in two places, so adding a future envelope header
 * to one without the other could silently re-open the spoof gap that
 * round-6 closed.
 *
 * Round-3 ultrareview C1 fix: the allowlist was missing
 * ``x-user-system-high``, ``x-user-workroom-role``, and the
 * ``x-user-workroom-id`` alias kept by the platform during the
 * workroom-id migration window.
 *
 * The HMAC pair (``x-user-signature`` / ``x-user-signature-ts``) is part
 * of the platform-internal envelope but extensions deliberately don't
 * verify it (§4.4.2 revised 2026-04-23). We forward them anyway so the
 * envelope arrives intact at the backend, in case a future
 * platform-side check inspects them.
 */
const FORWARD_REQUEST_HEADERS = new Set<string>([
    ...ENVELOPE_AUTH_HEADERS,
    // Transport / tracing headers — not auth-bearing, not relevant
    // to the bridge's clear-and-synthesize cycle.
    "x-request-id",
    // ``cookie`` is forwarded verbatim from the incoming Next.js request
    // (the browser → Next.js hop) to the backend extension service.
    // The canonical extension auth surface is the envelope-header pair
    // (``x-user-id`` + ``authorization``/``x-auth-token``); ``cookie``
    // is forwarded only because some backend services use the platform
    // session cookie for compatibility with the legacy SDK proxy. The
    // response side strips ``set-cookie`` (DENY_RESPONSE_HEADERS), so a
    // backend service cannot mint or rotate cookies through this proxy.
    // If your extension doesn't need cookie passthrough, override
    // ``FORWARD_REQUEST_HEADERS`` in your ProxyConfig — round-6 H4
    // tracks tightening this default in a follow-up once the legacy
    // session-cookie consumers are inventoried.
    "cookie",
    "content-type",
]);

/** Response headers that must NOT be forwarded to the client. */
const DENY_RESPONSE_HEADERS = new Set([
    "x-powered-by",
    "server",
    "set-cookie",
    "x-aspnet-version",
    "x-aspnetmvc-version",
]);

type NextRequest = Request;
type NextResponse = Response;
type RouteHandler = (
    request: NextRequest,
    context?: { params?: Record<string, string | string[]> }
) => Promise<NextResponse>;

function buildForwardHeaders(incoming: Headers): Record<string, string> {
    const out: Record<string, string> = {};
    for (const name of FORWARD_REQUEST_HEADERS) {
        const val = incoming.get(name);
        if (val) out[name] = val;
    }
    return out;
}

function filterResponseHeaders(headers: Headers): Record<string, string> {
    const out: Record<string, string> = {};
    headers.forEach((value, key) => {
        if (!DENY_RESPONSE_HEADERS.has(key.toLowerCase())) {
            out[key] = value;
        }
    });
    return out;
}

/**
 * Validate and resolve the proxy target URL.
 *
 * Rejects path traversal, encoded traversal, and scheme injection.
 * Returns the resolved URL string or throws on invalid input.
 */
function resolveTarget(target: string, path: string, search: string): string {
    // Decode repeatedly to catch multi-layer encoding (%252e%252e → %2e%2e → ..)
    let decoded = path;
    for (let i = 0; i < 3; i++) {
        const next = decodeURIComponent(decoded);
        if (next === decoded) break;  // stable — no more encoded layers
        decoded = next;
    }

    // Reject path traversal sequences
    if (decoded.includes("..")) {
        throw new Error("Path traversal detected");
    }

    // Also reject %2e in the raw path as defense-in-depth
    if (/%2e/i.test(path)) {
        throw new Error("Path traversal detected");
    }

    // Reject absolute URLs / scheme injection in the path
    if (/^[a-z][a-z0-9+.-]*:/i.test(decoded)) {
        throw new Error("Scheme injection detected");
    }

    // Normalize: ensure path starts with /
    const safePath = path.startsWith("/") ? path : `/${path}`;

    const targetOrigin = new URL(target).origin;
    const resolved = new URL(`${targetOrigin}${safePath}${search}`);

    // Final origin check — resolved URL must match the configured target
    if (resolved.origin !== targetOrigin) {
        throw new Error("Resolved URL origin mismatch");
    }

    return resolved.toString();
}

function makeHandler(method: string, config: ProxyConfig): RouteHandler {
    // Pre-parse the target to fail fast on bad config
    const targetOrigin = new URL(config.target).origin;

    return async (request: NextRequest) => {
        const url = new URL(request.url);
        let path = url.pathname;

        // Strip the configured prefix so the backend sees clean paths.
        if (config.pathPrefix && path.startsWith(config.pathPrefix)) {
            path = path.slice(config.pathPrefix.length) || "/";
        }

        let target: string;
        try {
            target = resolveTarget(config.target, path, url.search);
        } catch {
            return new Response("Bad Request", { status: 400 });
        }

        const forwardHeaders = buildForwardHeaders(request.headers);

        const init: RequestInit = {
            method,
            headers: forwardHeaders,
        };

        // Forward body for methods that have one.
        if (method !== "GET" && method !== "HEAD") {
            init.body = request.body;
            // @ts-expect-error -- Node fetch supports duplex for streaming
            init.duplex = "half";
        }

        const upstream = await fetch(target, init);

        // Stream the response back, filtering sensitive headers.
        return new Response(upstream.body, {
            status: upstream.status,
            statusText: upstream.statusText,
            headers: filterResponseHeaders(upstream.headers),
        });
    };
}

/**
 * Create Next.js App Router route handlers that proxy to a backend.
 *
 * All auth headers are forwarded and response bodies are streamed.
 * Includes path traversal protection and response header filtering.
 *
 * ```ts
 * // app/api/[...path]/route.ts
 * import { createProxyHandlers } from "@kamiwaza-ai/extensions-lib/server";
 * const { GET, POST, PUT, DELETE } = createProxyHandlers({
 *     target: "http://backend:8000",
 * });
 * export { GET, POST, PUT, DELETE };
 * ```
 */
export function createProxyHandlers(config: ProxyConfig) {
    return {
        GET: makeHandler("GET", config),
        POST: makeHandler("POST", config),
        PUT: makeHandler("PUT", config),
        DELETE: makeHandler("DELETE", config),
        PATCH: makeHandler("PATCH", config),
    };
}

// Exported for testing
export {
    resolveTarget as _resolveTarget,
    filterResponseHeaders as _filterResponseHeaders,
    buildForwardHeaders as _buildForwardHeaders,
};
