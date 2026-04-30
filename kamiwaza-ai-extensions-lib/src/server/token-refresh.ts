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
    /**
     * Upper bound on the buffered request body, in bytes. Defaults to
     * ``DEFAULT_MAX_BUFFER_BYTES`` (8 MiB). Round-3 H1: a streamed
     * ``request.body`` from a Next.js Route Handler is buffered to bytes
     * up-front so the retry path can replay it. Without a cap, a
     * multi-hundred-MB upload would balloon process memory. Set higher
     * if you knowingly proxy large bodies AND accept the memory cost; or
     * route those endpoints around ``streamWithRefresh`` entirely (it's
     * sized for short-body chat-completions, not bulk uploads).
     */
    maxBufferBytes?: number;
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

function _hasHeader(headers: Headers, name: string): boolean {
    const lower = name.toLowerCase();
    return Object.keys(headers).some((k) => k.toLowerCase() === lower);
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
    const maxBytes = opts.maxBufferBytes ?? DEFAULT_MAX_BUFFER_BYTES;
    // Round-3 H1/H2: a one-shot ReadableStream body (the common
    // ``request.body`` case from a Next.js Route Handler) cannot be
    // re-played on retry, and Node's fetch requires ``duplex: "half"``
    // when sending a streamed request body. We buffer the body once
    // up-front (capped at ``maxBufferBytes``) so:
    //   1. fetch() doesn't reject for missing duplex on streams.
    //   2. The retry path can re-send identical bytes.
    //   3. FormData / URLSearchParams contribute their generated
    //      Content-Type (multipart boundary etc.) so the upstream sees
    //      a parseable body.
    // Static bodies (string / Buffer / Uint8Array) are already replayable;
    // we still normalize them so the call-site doesn't have to branch.
    const materialized = await _materializeBody(opts.body, maxBytes);
    const effectiveHeaders: Headers = { ...headers };
    if (materialized?.contentType && !_hasHeader(effectiveHeaders, "content-type")) {
        // FormData/URLSearchParams without a caller-supplied Content-Type:
        // inject the one fetch() would have generated.
        effectiveHeaders["Content-Type"] = materialized.contentType;
    }
    const fetchInit: RequestInit = { method, headers: effectiveHeaders, signal };
    if (materialized !== null) {
        fetchInit.body = materialized.bytes;
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
        const retryHeaders: Headers = { ...newHeaders };
        if (materialized?.contentType && !_hasHeader(retryHeaders, "content-type")) {
            retryHeaders["Content-Type"] = materialized.contentType;
        }
        const retryInit: RequestInit = { method, headers: retryHeaders, signal };
        if (materialized !== null) {
            retryInit.body = materialized.bytes;
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
 * Default upper bound on buffered request bodies (round-3 H1). 8 MiB is
 * generous for chat-completions JSON (typical < 100 KB) and small file
 * uploads, and small enough to fail loudly before a Next.js Route Handler
 * process balloons on a multi-hundred-MB upload. Override per-call via
 * ``ProxyOpts.maxBufferBytes``.
 */
export const DEFAULT_MAX_BUFFER_BYTES = 8 * 1024 * 1024;

export class BodyTooLargeError extends Error {
    static readonly className = "body_too_large";
    constructor(public readonly limit: number) {
        super(
            `request body exceeds streamWithRefresh's buffering cap of ${limit} bytes; ` +
                `pass a larger maxBufferBytes or refactor the route to bypass this helper`,
        );
        this.name = "BodyTooLargeError";
    }
}

/**
 * Materialize a request body so the retry path can replay it and Node's
 * fetch doesn't reject for a missing ``duplex: "half"`` (round-3 H1/H2).
 *
 * Accepts everything ``BodyInit`` accepts. Returns ``null`` for null /
 * undefined so the caller knows to omit ``body`` from the RequestInit.
 *
 * Caps total buffered size at ``maxBufferBytes``. Throws
 * :class:`BodyTooLargeError` early rather than letting a malicious or
 * mistakenly-large request OOM the process. For ReadableStream input we
 * enforce the cap incrementally (no reading past the limit) so a 10 GB
 * body doesn't even get drained.
 *
 * For ``FormData`` / ``URLSearchParams`` we *also* return the
 * ``Content-Type`` header that ``fetch()`` would have stamped (multipart
 * boundary or ``application/x-www-form-urlencoded``). The caller merges
 * it into headers before fetch, so the upstream sees a valid body —
 * round-3 H2.
 */
export interface MaterializedBody {
    bytes: Uint8Array;
    contentType: string | null;
}

async function _materializeBody(
    body: BodyInit | null | undefined,
    maxBufferBytes: number,
): Promise<MaterializedBody | null> {
    if (body === null || body === undefined) return null;

    // Already-materialized shapes — no Content-Type forced; caller's
    // headers carry it.
    if (body instanceof Uint8Array) {
        _ensureUnderLimit(body.byteLength, maxBufferBytes);
        return { bytes: body, contentType: null };
    }
    if (body instanceof ArrayBuffer) {
        _ensureUnderLimit(body.byteLength, maxBufferBytes);
        return { bytes: new Uint8Array(body), contentType: null };
    }
    if (typeof body === "string") {
        const encoded = new TextEncoder().encode(body);
        _ensureUnderLimit(encoded.byteLength, maxBufferBytes);
        return { bytes: encoded, contentType: null };
    }
    if (body instanceof Blob) {
        _ensureUnderLimit(body.size, maxBufferBytes);
        return {
            bytes: new Uint8Array(await body.arrayBuffer()),
            // Blob's type is the Content-Type it was constructed with; fetch
            // would have set this, so propagate.
            contentType: body.type || null,
        };
    }
    if (body instanceof ReadableStream) {
        // Drain incrementally so we can fail BEFORE consuming a 10 GB body.
        const chunks: Uint8Array[] = [];
        let total = 0;
        const reader = body.getReader();
        for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            if (value === undefined) continue;
            total += value.byteLength;
            if (total > maxBufferBytes) {
                // Cancel upstream so the producer can stop.
                reader.cancel().catch(() => {});
                throw new BodyTooLargeError(maxBufferBytes);
            }
            chunks.push(value);
        }
        const out = new Uint8Array(total);
        let off = 0;
        for (const c of chunks) {
            out.set(c, off);
            off += c.byteLength;
        }
        return { bytes: out, contentType: null };
    }
    if (body instanceof FormData || body instanceof URLSearchParams) {
        // Round-3 H2: bare FormData / URLSearchParams need the runtime
        // to generate a matching Content-Type (multipart boundary etc.)
        // and that header must reach the upstream. Naive materialization
        // produces the body bytes but loses the boundary, so the upstream
        // fails to parse. Rather than attempt to serialize in-line (which
        // is environment-fragile across Node fetch / undici / jsdom),
        // refuse the input and tell the caller to pre-serialize.
        //
        // The dominant Next.js Route Handler shape is forwarding
        // ``request.body`` (a ReadableStream) — that path is unaffected.
        throw new Error(
            "streamWithRefresh: bare FormData/URLSearchParams bodies are not " +
                "supported. Serialize via `await new Response(body).arrayBuffer()` " +
                "and set the Content-Type header from `new Response(body).headers` " +
                "before calling. (Forwarding `request.body` from a Route Handler " +
                "works as-is — only direct FormData callers hit this path.)",
        );
    }
    // Fallback: let Response coerce whatever we got.
    const fallback = new Uint8Array(await new Response(body as BodyInit).arrayBuffer());
    _ensureUnderLimit(fallback.byteLength, maxBufferBytes);
    return { bytes: fallback, contentType: null };
}

function _ensureUnderLimit(size: number, limit: number): void {
    if (size > limit) {
        throw new BodyTooLargeError(limit);
    }
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
