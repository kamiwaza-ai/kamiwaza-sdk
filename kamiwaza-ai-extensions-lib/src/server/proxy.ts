import { ENVELOPE_AUTH_HEADERS } from "../_shared/envelopeHeaders";
import { resolveRuntimeRouting, withAppPath } from "../runtime/shared";
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
    // response side strips ``set-cookie`` unless a proxy instance explicitly
    // opts a trusted session route in, where cookies are rebased to the
    // deployment path.
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
// These proxy handlers ignore the route `context`/`params` entirely. Next 14
// passes `params` synchronously while Next 15 made it a Promise, so any typed
// second parameter is a cross-major compatibility hazard against Next's
// generated route-handler constraint. A handler that declares only `request`
// is assignable to Next's `(request, context) => ...` route type on BOTH
// majors (a function with fewer parameters is assignable), so we omit the
// second parameter rather than try to model the per-version `params` shape.
type RouteHandler = (request: NextRequest) => Promise<NextResponse>;

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

const DEFAULT_SET_COOKIE_PATHS: readonly string[] = [];

/**
 * Resolve the deployment's runtime app path from the same fail-closed
 * environment contract used by the runtime-config route.
 */
function resolveRuntimeAppPath(): string {
    return resolveRuntimeRouting(process.env).appPath;
}

/** Remove at most one leading, segment-boundary occurrence of prefix. */
function stripOnce(path: string, prefix: string): string {
    if (prefix === "") return path;
    if (path === prefix) return "/";
    if (path.startsWith(`${prefix}/`)) return path.slice(prefix.length);
    return path;
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

    const configuredTarget = new URL(target);
    const targetOrigin = configuredTarget.origin;
    const basePath = configuredTarget.pathname.replace(/\/+$/, "");
    const resolved = new URL(configuredTarget);
    resolved.pathname = `${basePath}${safePath}`;
    resolved.search = search;
    resolved.hash = "";

    // Final origin check — resolved URL must match the configured target
    if (resolved.origin !== targetOrigin) {
        throw new Error("Resolved URL origin mismatch");
    }

    return resolved.toString();
}

function scopeSetCookie(cookie: string, appPath: string): string {
    const cookiePath = appPath || "/";
    const [pair, ...rawAttributes] = cookie.split(";");
    const equals = pair.indexOf("=");
    const cookieName = (equals === -1 ? pair : pair.slice(0, equals)).trim();
    if (cookiePath !== "/" && cookieName.startsWith("__Host-")) {
        throw new Error(
            `cannot scope ${cookieName} to ${cookiePath}: __Host- cookies require Path=/`,
        );
    }
    // Attribute names are whitespace-trimmed by browsers. Parse them before
    // filtering so forms such as `Domain =example.com` cannot bypass scope
    // hardening, then append one authoritative deployment Path.
    const safeAttributes = rawAttributes
        .map((attribute) => attribute.trim())
        .filter((attribute) => {
            const separator = attribute.indexOf("=");
            const name = (separator === -1 ? attribute : attribute.slice(0, separator))
                .trim()
                .toLowerCase();
            return name !== "path" && name !== "domain";
        })
        .filter(Boolean);
    return [pair.trim(), ...safeAttributes, `Path=${cookiePath}`].join("; ");
}

function rebaseLocation(headers: Headers, appPath: string, target: string): void {
    const location = headers.get("location");
    if (location == null) {
        return;
    }
    const configuredTarget = new URL(target);
    const targetPath = configuredTarget.pathname.replace(/\/+$/, "");
    if (location.startsWith("/") && !location.startsWith("//")) {
        const redirected = new URL(location, configuredTarget);
        const redirectPath = stripOnce(redirected.pathname, targetPath).replace(
            /^\/+/,
            "/",
        );
        const local = `${redirectPath}${redirected.search}${redirected.hash}`;
        headers.set("location", withAppPath(local, appPath));
        return;
    }
    if (!URL.canParse(location)) {
        return;
    }
    const redirected = new URL(location);
    if (redirected.origin !== configuredTarget.origin) {
        return;
    }
    const redirectPath = stripOnce(redirected.pathname, targetPath).replace(/^\/+/, "/");
    const local = `${redirectPath}${redirected.search}${redirected.hash}`;
    headers.set("location", withAppPath(local, appPath));
}

function makeHandler(
    method: string,
    config: ProxyConfig,
    runtimeAppPath: string,
): RouteHandler {
    // Pre-parse the target to fail fast on bad config
    new URL(config.target);

    return async (request: NextRequest) => {
        const url = new URL(request.url);
        // Prefer Next's normalized URL (basePath already removed) when the
        // handler runs inside Next; fall back to the raw request URL.
        const nextUrl = (request as { nextUrl?: URL }).nextUrl;
        let path = nextUrl?.pathname ?? url.pathname;
        const search = nextUrl?.search ?? url.search;

        // Strip the deployment's runtime app path (default on). Next's own
        // basePath routing usually strips it first, making this a no-op; the
        // raw-URL path covers everything else.
        if (config.stripRuntimeAppPath !== false) {
            path = stripOnce(path, runtimeAppPath);
        }

        // Strip the configured prefix so the backend sees clean paths.
        if (config.pathPrefix && path.startsWith(config.pathPrefix)) {
            path = path.slice(config.pathPrefix.length) || "/";
        }

        let target: string;
        try {
            target = resolveTarget(config.target, path, search);
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
        const responseHeaders = new Headers(filterResponseHeaders(upstream.headers));
        rebaseLocation(responseHeaders, runtimeAppPath, config.target);

        // Set-Cookie passes through only for the explicit session-route
        // allowlist (matched on the backend-facing path), and only from the
        // configured trusted backend.
        const cookiePaths = config.setCookiePaths ?? DEFAULT_SET_COOKIE_PATHS;
        if (cookiePaths.includes(path)) {
            try {
                for (const cookie of upstream.headers.getSetCookie()) {
                    responseHeaders.append(
                        "set-cookie",
                        scopeSetCookie(cookie, runtimeAppPath),
                    );
                }
            } catch (error) {
                console.error(
                    "[kamiwaza-runtime] rejected an upstream cookie that cannot " +
                        "be scoped to this deployment",
                    error,
                );
                return new Response("Bad Gateway: incompatible upstream cookie", {
                    status: 502,
                    headers: { "cache-control": "no-store" },
                });
            }
        }

        return new Response(upstream.body, {
            status: upstream.status,
            statusText: upstream.statusText,
            headers: responseHeaders,
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
    const runtimeAppPath = resolveRuntimeAppPath();
    return {
        GET: makeHandler("GET", config, runtimeAppPath),
        POST: makeHandler("POST", config, runtimeAppPath),
        PUT: makeHandler("PUT", config, runtimeAppPath),
        DELETE: makeHandler("DELETE", config, runtimeAppPath),
        PATCH: makeHandler("PATCH", config, runtimeAppPath),
    };
}

// Exported for testing
export {
    resolveTarget as _resolveTarget,
    filterResponseHeaders as _filterResponseHeaders,
    buildForwardHeaders as _buildForwardHeaders,
};
