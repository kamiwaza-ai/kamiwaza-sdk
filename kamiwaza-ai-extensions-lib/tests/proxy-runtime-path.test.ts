import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createProxyHandlers } from "../src/server/proxy";

const APP = "/runtime/apps/550e8400";
const TARGET = "http://backend:8000";

function upstream(body = "{}", headers?: HeadersInit): Response {
    return new Response(body, { status: 200, headers });
}

let fetchSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
    process.env.KAMIWAZA_ROUTING_MODE = "path";
    process.env.KAMIWAZA_APP_PATH = APP;
    fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(upstream());
});

afterEach(() => {
    delete process.env.KAMIWAZA_ROUTING_MODE;
    delete process.env.KAMIWAZA_APP_PATH;
    vi.restoreAllMocks();
});

function requestedUrl(): string {
    return fetchSpy.mock.calls.at(-1)?.[0] as string;
}

describe("createProxyHandlers runtime app path stripping", () => {
    it("strips the deployment prefix from the raw request URL", async () => {
        const { GET } = createProxyHandlers({ target: TARGET });
        await GET(new Request(`http://localhost${APP}/api/things?x=1`));
        expect(requestedUrl()).toBe(`${TARGET}/api/things?x=1`);
    });

    it("prefers a Next-normalized nextUrl pathname when present", async () => {
        const { GET } = createProxyHandlers({ target: TARGET });
        const request = new Request(`http://localhost${APP}/api/things`);
        Object.defineProperty(request, "nextUrl", {
            value: new URL("http://localhost/api/things"),
        });
        await GET(request);
        expect(requestedUrl()).toBe(`${TARGET}/api/things`);
    });

    it("strips at most one occurrence of the prefix", async () => {
        const { GET } = createProxyHandlers({ target: TARGET });
        await GET(new Request(`http://localhost${APP}${APP}/api/things`));
        expect(requestedUrl()).toBe(`${TARGET}${APP}/api/things`);
    });

    it("does not strip across segment boundaries", async () => {
        const { GET } = createProxyHandlers({ target: TARGET });
        await GET(new Request(`http://localhost${APP}beef/api/things`));
        expect(requestedUrl()).toBe(`${TARGET}${APP}beef/api/things`);
    });

    it("can be disabled explicitly", async () => {
        const { GET } = createProxyHandlers({ target: TARGET, stripRuntimeAppPath: false });
        await GET(new Request(`http://localhost${APP}/api/things`));
        expect(requestedUrl()).toBe(`${TARGET}${APP}/api/things`);
    });

    it("is a no-op in port mode", async () => {
        process.env.KAMIWAZA_ROUTING_MODE = "port";
        const { GET } = createProxyHandlers({ target: TARGET });
        await GET(new Request("http://localhost/api/things"));
        expect(requestedUrl()).toBe(`${TARGET}/api/things`);
    });
});

describe("createProxyHandlers forwarded routing headers", () => {
    it("drops browser-supplied x-forwarded-* routing context", async () => {
        const { GET } = createProxyHandlers({ target: TARGET });
        await GET(
            new Request(`http://localhost${APP}/api/things`, {
                headers: {
                    "x-forwarded-prefix": APP,
                    "x-forwarded-host": "host.example",
                    "x-forwarded-proto": "https",
                    "x-forwarded-for": "10.0.0.1",
                    "x-forwarded-uri": `${APP}/api/things`,
                },
            }),
        );
        const headers = fetchSpy.mock.calls.at(-1)?.[1]?.headers as Record<string, string>;
        expect(headers["x-forwarded-prefix"]).toBeUndefined();
        expect(headers["x-forwarded-host"]).toBeUndefined();
        expect(headers["x-forwarded-proto"]).toBeUndefined();
        expect(headers["x-forwarded-for"]).toBeUndefined();
        expect(headers["x-forwarded-uri"]).toBeUndefined();
    });
});

describe("createProxyHandlers Set-Cookie policy", () => {
    const TWO_COOKIES = new Headers();
    TWO_COOKIES.append("set-cookie", "session=abc; Path=/; HttpOnly");
    TWO_COOKIES.append("set-cookie", "refresh=def; Path=/; HttpOnly");

    it("passes multiple Set-Cookie values through for allowlisted session routes", async () => {
        fetchSpy.mockResolvedValue(upstream("{}", TWO_COOKIES));
        const { POST } = createProxyHandlers({
            target: TARGET,
            setCookiePaths: ["/session"],
        });
        const response = await POST(new Request(`http://localhost${APP}/session`, { method: "POST" }));
        expect(response.headers.getSetCookie()).toEqual([
            `session=abc; HttpOnly; Path=${APP}`,
            `refresh=def; HttpOnly; Path=${APP}`,
        ]);
    });

    it("adds a deployment-scoped Path when the backend omits one", async () => {
        fetchSpy.mockResolvedValue(
            upstream("{}", { "set-cookie": "session=abc; HttpOnly; SameSite=Lax" }),
        );
        const { POST } = createProxyHandlers({
            target: TARGET,
            setCookiePaths: ["/session"],
        });
        const response = await POST(
            new Request(`http://localhost${APP}/session`, { method: "POST" }),
        );
        expect(response.headers.get("set-cookie")).toBe(
            `session=abc; HttpOnly; SameSite=Lax; Path=${APP}`,
        );
    });

    it("removes duplicate Path and Domain attributes before scoping", async () => {
        fetchSpy.mockResolvedValue(
            upstream("{}", {
                "set-cookie":
                    "session=abc; Path=/legacy; Domain=example.test; HttpOnly; Path=/",
            }),
        );
        const { POST } = createProxyHandlers({
            target: TARGET,
            setCookiePaths: ["/session"],
        });
        const response = await POST(
            new Request(`http://localhost${APP}/session`, { method: "POST" }),
        );
        expect(response.headers.get("set-cookie")).toBe(
            `session=abc; HttpOnly; Path=${APP}`,
        );
    });

    it("strips whitespace-padded cookie scope attributes", async () => {
        fetchSpy.mockResolvedValue(
            upstream("{}", {
                "set-cookie": "session=abc; Domain =evil.example; Path\t=/wide; HttpOnly",
            }),
        );
        const { POST } = createProxyHandlers({
            target: TARGET,
            setCookiePaths: ["/session"],
        });
        const response = await POST(
            new Request(`http://localhost${APP}/session`, { method: "POST" }),
        );
        expect(response.headers.get("set-cookie")).toBe(
            `session=abc; HttpOnly; Path=${APP}`,
        );
    });

    it("matches the cookie allowlist before adding a target path prefix", async () => {
        fetchSpy.mockResolvedValue(
            upstream("{}", { "set-cookie": "session=abc; Path=/; HttpOnly" }),
        );
        const { POST } = createProxyHandlers({
            target: `${TARGET}/v1`,
            setCookiePaths: ["/session"],
        });
        const response = await POST(
            new Request(`http://localhost${APP}/session`, { method: "POST" }),
        );
        expect(requestedUrl()).toBe(`${TARGET}/v1/session`);
        expect(response.headers.get("set-cookie")).toBe(
            `session=abc; HttpOnly; Path=${APP}`,
        );
    });

    it("returns a diagnostic 502 instead of emitting an invalid __Host- cookie", async () => {
        const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
        fetchSpy.mockResolvedValue(
            upstream("{}", {
                "set-cookie": "__Host-session=abc; Path=/; Secure; HttpOnly",
            }),
        );
        const { POST } = createProxyHandlers({
            target: TARGET,
            setCookiePaths: ["/session"],
        });
        const response = await POST(
            new Request(`http://localhost${APP}/session`, { method: "POST" }),
        );

        expect(response.status).toBe(502);
        expect(await response.text()).toMatch(/incompatible upstream cookie/i);
        expect(response.headers.getSetCookie()).toHaveLength(0);
        expect(errorSpy).toHaveBeenCalledWith(
            expect.stringMatching(/rejected an upstream cookie/i),
            expect.any(Error),
        );
    });

    it("drops Set-Cookie by default even on a standard session path", async () => {
        fetchSpy.mockResolvedValue(upstream("{}", TWO_COOKIES));
        const { GET } = createProxyHandlers({ target: TARGET });
        const response = await GET(new Request(`http://localhost${APP}/session`));
        expect(response.headers.getSetCookie()).toHaveLength(0);
    });

    it("honors a custom allowlist", async () => {
        fetchSpy.mockImplementation(() => Promise.resolve(upstream("{}", TWO_COOKIES)));
        const { POST } = createProxyHandlers({
            target: TARGET,
            setCookiePaths: ["/custom/login"],
        });
        const allowed = await POST(
            new Request(`http://localhost${APP}/custom/login`, { method: "POST" }),
        );
        expect(allowed.headers.getSetCookie()).toHaveLength(2);

        const denied = await POST(new Request(`http://localhost${APP}/session`, { method: "POST" }));
        expect(denied.headers.getSetCookie()).toHaveLength(0);
    });
});

describe("createProxyHandlers redirects and target paths", () => {
    it("rebases root-relative Location headers under the deployment path", async () => {
        fetchSpy.mockResolvedValue(upstream("", { location: "/login" }));
        const { GET } = createProxyHandlers({ target: TARGET });
        const response = await GET(new Request(`http://localhost${APP}/session`));
        expect(response.headers.get("location")).toBe(`${APP}/login`);
    });

    it("preserves a configured backend target path prefix", async () => {
        const { GET } = createProxyHandlers({ target: `${TARGET}/v1` });
        await GET(new Request(`http://localhost${APP}/api/things?x=1`));
        expect(requestedUrl()).toBe(`${TARGET}/v1/api/things?x=1`);
    });

    it("strips a configured backend prefix from root-relative redirects", async () => {
        fetchSpy.mockResolvedValue(
            upstream("", { location: "/v1/login?next=%2Fdashboard" }),
        );
        const { GET } = createProxyHandlers({ target: `${TARGET}/v1` });
        const response = await GET(new Request(`http://localhost${APP}/session`));
        expect(response.headers.get("location")).toBe(
            `${APP}/login?next=%2Fdashboard`,
        );
    });

    it("rebases absolute redirects back to the trusted target origin", async () => {
        fetchSpy.mockResolvedValue(
            upstream("", { location: `${TARGET}/v1/login?next=%2Fdashboard` }),
        );
        const { GET } = createProxyHandlers({ target: `${TARGET}/v1` });
        const response = await GET(new Request(`http://localhost${APP}/session`));
        expect(response.headers.get("location")).toBe(
            `${APP}/login?next=%2Fdashboard`,
        );
    });

    it("leaves external absolute redirects untouched", async () => {
        fetchSpy.mockResolvedValue(
            upstream("", { location: "https://identity.example/login" }),
        );
        const { GET } = createProxyHandlers({ target: TARGET });
        const response = await GET(new Request(`http://localhost${APP}/session`));
        expect(response.headers.get("location")).toBe(
            "https://identity.example/login",
        );
    });
});
