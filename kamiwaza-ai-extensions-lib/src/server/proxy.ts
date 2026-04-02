import type { ProxyConfig } from "./types";

/** Headers to forward from the incoming request to the backend. */
const AUTH_HEADERS = [
    "authorization",
    "x-auth-token",
    "x-user-id",
    "x-user-email",
    "x-user-name",
    "x-user-roles",
    "x-workroom-id",
    "x-request-id",
    "cookie",
    "content-type",
];

type NextRequest = Request;
type NextResponse = Response;
type RouteHandler = (
    request: NextRequest,
    context?: { params?: Record<string, string | string[]> }
) => Promise<NextResponse>;

function buildForwardHeaders(incoming: Headers): Record<string, string> {
    const out: Record<string, string> = {};
    for (const name of AUTH_HEADERS) {
        const val = incoming.get(name);
        if (val) out[name] = val;
    }
    return out;
}

function makeHandler(method: string, config: ProxyConfig): RouteHandler {
    return async (request: NextRequest) => {
        const url = new URL(request.url);
        let path = url.pathname;

        // Strip the configured prefix so the backend sees clean paths.
        if (config.pathPrefix && path.startsWith(config.pathPrefix)) {
            path = path.slice(config.pathPrefix.length) || "/";
        }

        const target = `${config.target.replace(/\/+$/, "")}${path}${url.search}`;
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

        // Stream the response back to the client.
        return new Response(upstream.body, {
            status: upstream.status,
            statusText: upstream.statusText,
            headers: Object.fromEntries(upstream.headers.entries()),
        });
    };
}

/**
 * Create Next.js App Router route handlers that proxy to a backend.
 *
 * All auth headers are forwarded and response bodies are streamed.
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
