import type { ProxyConfig } from "./types";
import { getLocalDevAuthHeaders } from "./localDevAuth";

/** Headers to forward from the incoming request to the backend.
 *  Auth is header-based (Authorization / X-*), not cookie-based,
 *  so cookies are intentionally excluded to avoid leaking browser
 *  session cookies to the backend while stripping set-cookie from
 *  responses.
 */
const FORWARD_REQUEST_HEADERS = new Set([
    "authorization",
    "x-auth-token",
    "x-user-id",
    "x-user-email",
    "x-user-name",
    "x-user-roles",
    "x-workroom-id",
    "x-request-id",
    "content-type",
]);

const AUTH_REQUEST_HEADERS = new Set([
    "authorization",
    "x-auth-token",
    "x-user-id",
    "x-user-email",
    "x-user-name",
    "x-user-roles",
    "x-workroom-id",
    "x-request-id",
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
    let hasAuthHeaders = false;
    for (const name of FORWARD_REQUEST_HEADERS) {
        const val = incoming.get(name);
        if (!val) continue;
        out[name] = val;
        if (AUTH_REQUEST_HEADERS.has(name)) {
            hasAuthHeaders = true;
        }
    }
    if (hasAuthHeaders) {
        return out;
    }
    return { ...out, ...getLocalDevAuthHeaders() };
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

    // Also reject encoded dot-dot (%2e%2e or mixed) in the raw path
    if (/%2e[./\\%]|[./\\]%2e/i.test(path)) {
        throw new Error("Path traversal detected");
    }

    // Reject absolute URLs / scheme injection in the path
    if (/^[a-z][a-z0-9+.-]*:/i.test(decoded)) {
        throw new Error("Scheme injection detected");
    }

    // Normalize: ensure path starts with /
    const safePath = path.startsWith("/") ? path : `/${path}`;

    // Preserve any path prefix in the target (e.g., http://backend:8000/api/v1)
    const targetUrl = new URL(target);
    const targetBase = `${targetUrl.origin}${targetUrl.pathname.replace(/\/+$/, "")}`;
    const resolved = new URL(`${targetBase}${safePath}${search}`);

    // Final origin check — resolved URL must match the configured target
    if (resolved.origin !== targetUrl.origin) {
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
export { resolveTarget as _resolveTarget, filterResponseHeaders as _filterResponseHeaders };
