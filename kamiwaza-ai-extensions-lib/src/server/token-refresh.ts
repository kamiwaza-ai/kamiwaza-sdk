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
 * Each caller invokes ``refresh`` with its own headers (one refresh per
 * caller, not shared across requests). A previous version cached the
 * refresh Promise at module scope to deduplicate concurrent refreshes,
 * but that fanned a single user's refreshed headers out to other concurrent
 * requests in a multi-tenant Next.js process — a cross-user credential
 * mixup. The Python sibling (``kamiwaza_extensions_lib.middleware``) takes
 * the same approach: serializing through an ``asyncio.Lock`` would also
 * mix users; we just let each caller refresh its own token.
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

/**
 * Stream an upstream response with one transparent token-refresh retry.
 *
 * Returns a Response whose body streams from the upstream. The caller
 * (typically a Next.js Route Handler) returns this Response directly to
 * the extension client.
 */
export async function streamWithRefresh(opts: ProxyOpts): Promise<Response> {
    const { url, method, headers, refresh, signal } = opts;
    // PR-86 H1/H2: a one-shot ReadableStream body (the common
    // ``request.body`` case from a Next.js Route Handler) cannot be
    // re-played on retry, and Node's fetch requires ``duplex: "half"``
    // when sending a streamed request body. We buffer the body once
    // up-front so:
    //   1. fetch() doesn't reject for missing duplex on streams.
    //   2. The retry path can re-send identical bytes.
    // Static bodies (string / Buffer / Uint8Array / null) are already
    // re-playable; we still normalize them to a Uint8Array so the
    // call-site doesn't have to branch.
    const bodyBytes = await _materializeBody(opts.body);
    const fetchInit: RequestInit = { method, headers, signal };
    if (bodyBytes !== null) {
        fetchInit.body = bodyBytes;
    }

    let upstream = await fetch(url, fetchInit);

    if (upstream.status === 401) {
        // PRE_COMMIT_401 — drain the small error body to release the
        // connection, then refresh + retry once.
        try {
            await upstream.arrayBuffer();
        } catch {
            // best-effort; an error here doesn't change the retry decision
        }
        // Each caller refreshes with its own headers — no cross-request
        // Promise cache (see module docstring).
        const newHeaders = await refresh(headers);
        if (newHeaders === null) {
            throw new PlatformOutageError(
                "upstream 401 and no refresh token available",
            );
        }
        const retryInit: RequestInit = { method, headers: newHeaders, signal };
        if (bodyBytes !== null) {
            retryInit.body = bodyBytes;
        }
        upstream = await fetch(url, retryInit);
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

    // Translate post-commit upstream failures into a typed
    // StreamInterruptedError. The HTTP status is already sealed (we
    // returned a Response with ``upstream.status``), so we can't change
    // the status code; what we can do is fail the body's ReadableStream
    // with our typed error, so a stream consumer reading with try/catch
    // surfaces the runtime-lib class rather than a raw network error.
    //
    // We consume ``upstream.body`` ourselves (rather than via TransformStream)
    // so the catch block can shape the upstream rejection into our typed
    // error before it reaches the consumer's reader.read() rejection.
    const reader = upstream.body.getReader();
    const translatedStream = new ReadableStream<Uint8Array>({
        async pull(controller) {
            try {
                const { value, done } = await reader.read();
                if (done) {
                    controller.close();
                    return;
                }
                controller.enqueue(value);
            } catch (err) {
                controller.error(
                    new StreamInterruptedError(
                        `upstream stream aborted mid-flight: ${
                            err instanceof Error ? err.message : String(err)
                        }`,
                    ),
                );
            }
        },
        cancel(reason) {
            reader.cancel(reason).catch(() => {
                // best-effort
            });
        },
    });

    return new Response(translatedStream, {
        status: upstream.status,
        headers: passthroughHeaders(upstream.headers),
    });
}

/**
 * Buffer a request body to bytes so we can replay it on retry and avoid
 * Node's ``duplex: "half"`` requirement for streamed bodies (PR-86 H1/H2).
 *
 * Accepts everything ``BodyInit`` accepts. Returns ``null`` for null /
 * undefined so the caller knows to omit ``body`` from the RequestInit.
 */
async function _materializeBody(
    body: BodyInit | null | undefined,
): Promise<Uint8Array | null> {
    if (body === null || body === undefined) return null;
    if (body instanceof Uint8Array) return body;
    if (body instanceof ArrayBuffer) return new Uint8Array(body);
    if (typeof body === "string") return new TextEncoder().encode(body);
    if (body instanceof Blob) {
        return new Uint8Array(await body.arrayBuffer());
    }
    if (body instanceof ReadableStream) {
        // Drain the stream into a single buffer. Lossless for retry.
        const chunks: Uint8Array[] = [];
        const reader = body.getReader();
        for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            if (value !== undefined) chunks.push(value);
        }
        let total = 0;
        for (const c of chunks) total += c.byteLength;
        const out = new Uint8Array(total);
        let off = 0;
        for (const c of chunks) {
            out.set(c, off);
            off += c.byteLength;
        }
        return out;
    }
    if (body instanceof FormData || body instanceof URLSearchParams) {
        // Use the standard Request constructor to serialize these into a
        // single buffer the same way fetch() would have.
        return new Uint8Array(await new Response(body).arrayBuffer());
    }
    // Fallback: let Response coerce whatever we got.
    return new Uint8Array(await new Response(body as BodyInit).arrayBuffer());
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
