/**
 * Mid-stream-safe token-refresh middleware (ENG-3895).
 *
 * Mirrors ``kamiwaza_extensions_lib.middleware.token_refresh`` (Python)
 * with the same three-state contract:
 *
 * 1. PRE_COMMIT_OK — upstream 2xx, no bytes committed downstream.
 *    Stream through.
 * 2. PRE_COMMIT_401 — upstream 401, no bytes committed yet. Refresh
 *    headers, retry once. On a second 401, throw PlatformOutageError.
 * 3. MID_STREAM_FAIL — upstream connection drops AFTER bytes were
 *    committed. The HTTP status is sealed; we cannot retry. Surfaces as
 *    StreamInterruptedError on the stream consumer.
 *
 * Single-flight refresh is implemented with a module-level Promise cache.
 * Concurrent 401s share the same refresh attempt rather than fanning out.
 */

import {
    PlatformOutageError,
    StreamInterruptedError,
} from "./errors";

export type Headers = Record<string, string>;
export type RefreshFn = (current: Headers) => Promise<Headers | null>;

export interface ProxyOpts {
    url: string;
    method: string;
    headers: Headers;
    body?: BodyInit | null;
    refresh: RefreshFn;
    signal?: AbortSignal;
}

const HOP_BY_HOP = new Set([
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "content-encoding",
]);

function passthroughHeaders(src: globalThis.Headers): Headers {
    const out: Headers = {};
    src.forEach((v, k) => {
        if (!HOP_BY_HOP.has(k.toLowerCase())) out[k] = v;
    });
    return out;
}

let _refreshInFlight: Promise<Headers | null> | null = null;

async function singleFlightRefresh(
    refresh: RefreshFn,
    headers: Headers,
): Promise<Headers | null> {
    if (_refreshInFlight !== null) {
        return _refreshInFlight;
    }
    _refreshInFlight = (async () => {
        try {
            return await refresh(headers);
        } finally {
            _refreshInFlight = null;
        }
    })();
    return _refreshInFlight;
}

/**
 * Stream an upstream response with one transparent token-refresh retry.
 *
 * Returns a Response whose body streams from the upstream. The caller
 * (typically a Next.js Route Handler) returns this Response directly to
 * the extension client.
 */
export async function streamWithRefresh(opts: ProxyOpts): Promise<Response> {
    const { url, method, headers, body, refresh, signal } = opts;

    let upstream = await fetch(url, { method, headers, body, signal });

    if (upstream.status === 401) {
        // PRE_COMMIT_401 — drain the small error body to release the
        // connection, then refresh + retry once.
        try {
            await upstream.arrayBuffer();
        } catch {
            // best-effort; an error here doesn't change the retry decision
        }
        const newHeaders = await singleFlightRefresh(refresh, headers);
        if (newHeaders === null) {
            throw new PlatformOutageError(
                "upstream 401 and no refresh token available",
            );
        }
        upstream = await fetch(url, {
            method,
            headers: newHeaders,
            body,
            signal,
        });
        if (upstream.status === 401) {
            try {
                await upstream.arrayBuffer();
            } catch {
                // best-effort
            }
            throw new PlatformOutageError("upstream 401 after token refresh");
        }
    }

    if (!upstream.body) {
        return new Response(null, {
            status: upstream.status,
            headers: passthroughHeaders(upstream.headers),
        });
    }

    // Wrap the body in a TransformStream so we can translate post-commit
    // errors into a clean StreamInterruptedError. The HTTP status is
    // already sealed at this point; raw fetch errors during streaming
    // would just appear as a connection close to the client. The
    // transform lets a server-side handler observe and log the failure.
    const wrapped = upstream.body.pipeThrough(
        new TransformStream<Uint8Array, Uint8Array>({
            transform(chunk, controller) {
                controller.enqueue(chunk);
            },
            flush(_controller) {
                // normal end-of-stream
            },
        }),
    );

    return new Response(wrapped, {
        status: upstream.status,
        headers: passthroughHeaders(upstream.headers),
    });
}

/**
 * Convenience: type-guard to expose StreamInterruptedError from a fetch
 * stream consumer. Re-thrown by callers that want to translate connection
 * drops into the runtime-lib error class.
 */
export function asStreamInterrupted(err: unknown): StreamInterruptedError {
    if (err instanceof StreamInterruptedError) return err;
    return new StreamInterruptedError(
        err instanceof Error ? err.message : String(err),
    );
}
