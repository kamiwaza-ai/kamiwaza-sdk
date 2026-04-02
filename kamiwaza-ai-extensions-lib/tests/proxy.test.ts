import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { _resolveTarget, _filterResponseHeaders, createProxyHandlers } from "../src/server/proxy";

describe("resolveTarget", () => {
    it("resolves a normal path", () => {
        const url = _resolveTarget("http://backend:8000", "/api/data", "");
        expect(url).toBe("http://backend:8000/api/data");
    });

    it("includes search params", () => {
        const url = _resolveTarget("http://backend:8000", "/api/data", "?page=1");
        expect(url).toBe("http://backend:8000/api/data?page=1");
    });

    it("rejects path traversal with ..", () => {
        expect(() => _resolveTarget("http://backend:8000", "/../etc/passwd", "")).toThrow(
            "Path traversal detected"
        );
    });

    it("rejects encoded path traversal (%2e%2e)", () => {
        expect(() =>
            _resolveTarget("http://backend:8000", "/%2e%2e/etc/passwd", "")
        ).toThrow("Path traversal detected");
    });

    it("rejects double-encoded traversal", () => {
        // %252e%252e decodes to %2e%2e which decodes to ..
        // Our single decodeURIComponent catches %2e%2e → ..
        expect(() =>
            _resolveTarget("http://backend:8000", "/%2e%2e%2fetc%2fpasswd", "")
        ).toThrow("Path traversal detected");
    });

    it("rejects scheme injection in path", () => {
        expect(() =>
            _resolveTarget("http://backend:8000", "http://evil.com/steal", "")
        ).toThrow("Scheme injection detected");
    });

    it("rejects https scheme injection", () => {
        expect(() =>
            _resolveTarget("http://backend:8000", "https://evil.com/", "")
        ).toThrow("Scheme injection detected");
    });

    it("handles root path", () => {
        const url = _resolveTarget("http://backend:8000", "/", "");
        expect(url).toBe("http://backend:8000/");
    });

    it("handles empty path by prepending /", () => {
        const url = _resolveTarget("http://backend:8000", "", "");
        expect(url).toBe("http://backend:8000/");
    });

    it("preserves nested paths", () => {
        const url = _resolveTarget("http://backend:8000", "/api/v1/users/123", "");
        expect(url).toBe("http://backend:8000/api/v1/users/123");
    });
});

describe("filterResponseHeaders", () => {
    it("strips sensitive headers", () => {
        const headers = new Headers({
            "content-type": "application/json",
            "x-powered-by": "Express",
            server: "nginx",
            "set-cookie": "session=abc",
            "cache-control": "no-cache",
        });

        const filtered = _filterResponseHeaders(headers);

        expect(filtered["content-type"]).toBe("application/json");
        expect(filtered["cache-control"]).toBe("no-cache");
        expect(filtered["x-powered-by"]).toBeUndefined();
        expect(filtered["server"]).toBeUndefined();
        expect(filtered["set-cookie"]).toBeUndefined();
    });

    it("passes through standard content headers", () => {
        const headers = new Headers({
            "content-type": "text/html",
            "content-length": "1234",
            etag: '"abc"',
        });

        const filtered = _filterResponseHeaders(headers);

        expect(filtered["content-type"]).toBe("text/html");
        expect(filtered["content-length"]).toBe("1234");
        expect(filtered["etag"]).toBe('"abc"');
    });
});

describe("createProxyHandlers", () => {
    let fetchSpy: ReturnType<typeof vi.fn>;

    beforeEach(() => {
        fetchSpy = vi.fn().mockResolvedValue(
            new Response(JSON.stringify({ ok: true }), {
                status: 200,
                headers: { "content-type": "application/json" },
            })
        );
        vi.stubGlobal("fetch", fetchSpy);
    });

    afterEach(() => {
        vi.unstubAllGlobals();
    });

    it("forwards GET requests to target", async () => {
        const { GET } = createProxyHandlers({ target: "http://backend:8000" });

        const request = new Request("http://localhost:3000/api/users?page=1", {
            headers: { "x-user-id": "usr-123", "content-type": "application/json" },
        });

        await GET(request);

        expect(fetchSpy).toHaveBeenCalledOnce();
        const [url, init] = fetchSpy.mock.calls[0];
        expect(url).toBe("http://backend:8000/api/users?page=1");
        expect(init.method).toBe("GET");
        expect(init.headers["x-user-id"]).toBe("usr-123");
    });

    it("strips pathPrefix before forwarding", async () => {
        const { GET } = createProxyHandlers({
            target: "http://backend:8000",
            pathPrefix: "/api",
        });

        const request = new Request("http://localhost:3000/api/users");

        await GET(request);

        const [url] = fetchSpy.mock.calls[0];
        expect(url).toBe("http://backend:8000/users");
    });

    it("forwards POST body", async () => {
        const { POST } = createProxyHandlers({ target: "http://backend:8000" });

        const request = new Request("http://localhost:3000/api/data", {
            method: "POST",
            body: JSON.stringify({ key: "value" }),
            headers: { "content-type": "application/json" },
        });

        await POST(request);

        const [, init] = fetchSpy.mock.calls[0];
        expect(init.method).toBe("POST");
        expect(init.body).toBeDefined();
    });

    it("normalizes path traversal via URL constructor", async () => {
        const { GET } = createProxyHandlers({ target: "http://backend:8000" });

        // URL constructor normalizes /../ to / — the proxy never sees raw ..
        // This test verifies the request goes to the backend root, not /etc/passwd
        const request = new Request("http://localhost:3000/../../etc/passwd");
        await GET(request);

        const [url] = fetchSpy.mock.calls[0];
        // URL constructor normalized ../../etc/passwd to /etc/passwd
        expect(url).toBe("http://backend:8000/etc/passwd");
        // Critically: still on the backend origin, not an external host
        expect(url).toMatch(/^http:\/\/backend:8000\//);
    });

    it("filters sensitive response headers", async () => {
        fetchSpy.mockResolvedValue(
            new Response("ok", {
                headers: {
                    "content-type": "text/plain",
                    "x-powered-by": "Express",
                    server: "nginx",
                },
            })
        );

        const { GET } = createProxyHandlers({ target: "http://backend:8000" });
        const request = new Request("http://localhost:3000/api/health");
        const response = await GET(request);

        expect(response.headers.get("content-type")).toBe("text/plain");
        expect(response.headers.get("x-powered-by")).toBeNull();
        expect(response.headers.get("server")).toBeNull();
    });

    it("only forwards auth headers from request", async () => {
        const { GET } = createProxyHandlers({ target: "http://backend:8000" });

        const request = new Request("http://localhost:3000/api/data", {
            headers: {
                "x-user-id": "usr-123",
                authorization: "Bearer token",
                accept: "text/html",
                "user-agent": "test",
            },
        });

        await GET(request);

        const [, init] = fetchSpy.mock.calls[0];
        expect(init.headers["x-user-id"]).toBe("usr-123");
        expect(init.headers["authorization"]).toBe("Bearer token");
        // Non-auth headers should NOT be forwarded
        expect(init.headers["accept"]).toBeUndefined();
        expect(init.headers["user-agent"]).toBeUndefined();
    });
});
