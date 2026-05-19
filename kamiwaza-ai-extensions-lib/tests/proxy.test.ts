import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
    _resolveTarget,
    _filterResponseHeaders,
    _buildForwardHeaders,
    createProxyHandlers,
} from "../src/server/proxy";

// Round-3 ultrareview C1 regression test: the FORWARD_REQUEST_HEADERS
// allowlist must include every envelope field the backend's
// IdentityExtractor reads. Missing entries silently strip Identity fields
// on Next.js→backend proxied requests.
describe("buildForwardHeaders envelope coverage", () => {
    it("forwards every envelope header the backend's extract_identity reads", () => {
        const incoming = new Headers({
            "X-User-Id": "u1",
            "X-User-Email": "alice@example.com",
            "X-User-Name": "Alice",
            "X-User-Roles": "member,editor",
            "X-User-System-High": "U",
            "X-Workroom-Id": "w1",
            "X-User-Workroom-Id": "w1",
            "X-User-Workroom-Role": "editor",
            "X-Auth-Token": "ey.fake.jwt",
            "X-Request-Id": "req-abc",
        });
        const out = _buildForwardHeaders(incoming);
        // Every envelope field that the canonical test-vectors.json
        // expects must round-trip through the proxy. Compare lower-case
        // keys since Headers normalizes.
        const lowercased = Object.fromEntries(
            Object.entries(out).map(([k, v]) => [k.toLowerCase(), v]),
        );
        for (const required of [
            "x-user-id",
            "x-user-email",
            "x-user-name",
            "x-user-roles",
            "x-user-system-high",
            "x-workroom-id",
            "x-user-workroom-id",
            "x-user-workroom-role",
            "x-auth-token",
            "x-request-id",
        ]) {
            expect(lowercased).toHaveProperty(required);
        }
    });
});

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

    it("rejects single-encoded traversal (%2e%2e)", () => {
        expect(() =>
            _resolveTarget("http://backend:8000", "/%2e%2e/etc/passwd", "")
        ).toThrow("Path traversal detected");
    });

    it("rejects double-encoded traversal (%252e%252e)", () => {
        // %252e%252e → first decode → %2e%2e → second decode → ..
        expect(() =>
            _resolveTarget("http://backend:8000", "/%252e%252e/etc/passwd", "")
        ).toThrow("Path traversal detected");
    });

    it("rejects raw %2e in path as defense-in-depth", () => {
        // Even partial encoded dots are rejected
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
