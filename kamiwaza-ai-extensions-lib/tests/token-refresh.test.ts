import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { streamWithRefresh } from "../src/server/token-refresh";
import { PlatformOutageError, StreamInterruptedError } from "../src/server/errors";

// TS-M2-35..36: TypeScript mirror of the Python TokenRefreshMiddleware
// contract. Pre-commit 401 → refresh + retry; double-401 → PlatformOutageError.

const realFetch = global.fetch;

afterEach(() => {
    global.fetch = realFetch;
});

function mockFetch(responses: Response[]) {
    let i = 0;
    const stub = vi.fn(async (_url: string | URL, _init?: RequestInit) => {
        if (i >= responses.length) {
            throw new Error(`unexpected extra request #${i + 1}`);
        }
        return responses[i++];
    });
    global.fetch = stub as unknown as typeof fetch;
    return stub;
}

describe("streamWithRefresh", () => {
    beforeEach(() => {
        // Reset the module's single-flight cache by importing afresh —
        // the in-flight Promise is module-scoped so each test gets a clean
        // start.
    });

    it("TS-M2-35: pre-commit 401 → refresh → 200 stream", async () => {
        const stub = mockFetch([
            new Response("expired", { status: 401 }),
            new Response("data: chunk\n\n", { status: 200 }),
        ]);
        const refresh = vi.fn(async (h: Record<string, string>) => ({
            ...h,
            "X-Auth-Token": "new-token",
        }));

        const response = await streamWithRefresh({
            url: "https://upstream/v1/chat",
            method: "POST",
            headers: { "X-Auth-Token": "old-token" },
            refresh,
        });
        const body = await response.text();

        expect(response.status).toBe(200);
        expect(body).toContain("data: chunk");
        expect(refresh).toHaveBeenCalledTimes(1);
        // Second fetch carried the new token.
        expect(stub).toHaveBeenCalledTimes(2);
        const [, secondInit] = stub.mock.calls[1] as [
            unknown,
            RequestInit,
        ];
        const sentHeaders = (secondInit?.headers as Record<string, string>) || {};
        expect(sentHeaders["X-Auth-Token"]).toBe("new-token");
    });

    it("TS-M2-36: double-401 → PlatformOutageError (502 to caller)", async () => {
        mockFetch([
            new Response("", { status: 401 }),
            new Response("", { status: 401 }),
        ]);
        const refresh = vi.fn(async (h: Record<string, string>) => ({
            ...h,
            "X-Auth-Token": "new-token",
        }));

        await expect(
            streamWithRefresh({
                url: "https://upstream/v1/chat",
                method: "POST",
                headers: { "X-Auth-Token": "old-token" },
                refresh,
            }),
        ).rejects.toThrow(PlatformOutageError);
    });

    it("pre-commit 401 with no refresh available throws PlatformOutageError", async () => {
        mockFetch([new Response("", { status: 401 })]);

        await expect(
            streamWithRefresh({
                url: "https://upstream/v1/chat",
                method: "POST",
                headers: { "X-Auth-Token": "old" },
                refresh: async () => null,
            }),
        ).rejects.toThrow(PlatformOutageError);
    });

    it("post-commit upstream failure surfaces StreamInterruptedError (TS-M2-33 mirror)", async () => {
        // Build a Response whose body fails AFTER the consumer reads the
        // first chunk. We gate the error on the first read so the timing
        // is deterministic across event-loop variations between platforms.
        let firstRead = false;
        const failingBody = new ReadableStream<Uint8Array>({
            pull(controller) {
                if (!firstRead) {
                    firstRead = true;
                    controller.enqueue(new TextEncoder().encode("first-chunk"));
                    return;
                }
                controller.error(new Error("upstream dropped connection"));
            },
        });
        global.fetch = vi.fn(async () => {
            return new Response(failingBody, { status: 200 });
        }) as unknown as typeof fetch;

        const response = await streamWithRefresh({
            url: "https://upstream/v1/chat",
            method: "POST",
            headers: { "X-Auth-Token": "tok" },
            refresh: vi.fn(async () => null),
        });
        expect(response.status).toBe(200);

        // Drain via async iteration so the runtime sees the rejection as
        // a regular for-await throw — mirrors how a route handler would
        // typically forward the body downstream.
        const reader = response.body!.getReader();
        const chunks: string[] = [];
        let caught: unknown;
        try {
            while (true) {
                const { value, done } = await reader.read();
                if (done) break;
                chunks.push(new TextDecoder().decode(value));
            }
        } catch (err) {
            caught = err;
        }

        expect(chunks).toEqual(["first-chunk"]);
        expect(caught).toBeInstanceOf(StreamInterruptedError);
        expect((caught as Error).message).toContain("aborted mid-flight");
    });

    it("happy 200 passes through without invoking refresh", async () => {
        const stub = mockFetch([
            new Response("ok", { status: 200 }),
        ]);
        const refresh = vi.fn(async () => ({}));

        const response = await streamWithRefresh({
            url: "https://upstream/v1/chat",
            method: "GET",
            headers: { "X-Auth-Token": "tok" },
            refresh,
        });
        await response.text();

        expect(response.status).toBe(200);
        expect(refresh).not.toHaveBeenCalled();
        expect(stub).toHaveBeenCalledTimes(1);
    });
});
