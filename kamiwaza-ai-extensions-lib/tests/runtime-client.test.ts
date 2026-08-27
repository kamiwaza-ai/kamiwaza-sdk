import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
    appAsset,
    appFetch,
    getAppPath,
    loadKamiwazaRuntime,
    __resetKamiwazaRuntimeCacheForTests,
} from "../src/runtime/client";

const APP = "/runtime/apps/550e8400";

function setBootstrap(routingMode: "path" | "port", appPath: string): void {
    (globalThis as Record<string, unknown>).__KAMIWAZA_RUNTIME__ = Object.freeze({
        routingMode,
        appPath,
    });
}

beforeEach(() => {
    __resetKamiwazaRuntimeCacheForTests();
});

afterEach(() => {
    delete (globalThis as Record<string, unknown>).__KAMIWAZA_RUNTIME__;
    delete process.env.KZ_INTERNAL_BAKED_APP_PATH;
    vi.restoreAllMocks();
});

describe("getAppPath", () => {
    it("reads the inline bootstrap global when present", () => {
        setBootstrap("path", APP);
        expect(getAppPath()).toBe(APP);
    });

    it("falls back to the baked compile constant when no bootstrap exists", () => {
        process.env.KZ_INTERNAL_BAKED_APP_PATH = APP;
        expect(getAppPath()).toBe(APP);
    });

    it("returns empty string when nothing is configured", () => {
        expect(getAppPath()).toBe("");
    });

    it("fails closed on an inconsistent bootstrap global (N2)", () => {
        (globalThis as Record<string, unknown>).__KAMIWAZA_RUNTIME__ = Object.freeze({
            routingMode: "port",
            appPath: "/runtime/apps/x",
        });
        expect(() => getAppPath()).toThrow(/bootstrap|inconsistent/i);

        (globalThis as Record<string, unknown>).__KAMIWAZA_RUNTIME__ = Object.freeze({
            routingMode: "path",
            appPath: "",
        });
        expect(() => getAppPath()).toThrow(/bootstrap|inconsistent/i);
    });

    it("fails closed on an invalid bootstrap app path (N2)", () => {
        (globalThis as Record<string, unknown>).__KAMIWAZA_RUNTIME__ = Object.freeze({
            routingMode: "path",
            appPath: "/runtime/../etc",
        });
        expect(() => getAppPath()).toThrow();

        (globalThis as Record<string, unknown>).__KAMIWAZA_RUNTIME__ = Object.freeze({
            routingMode: "path",
            appPath: 42,
        });
        expect(() => getAppPath()).toThrow(/appPath|string/i);

        (globalThis as Record<string, unknown>).__KAMIWAZA_RUNTIME__ = Object.freeze({
            routingMode: "porta",
            appPath: APP,
        });
        expect(() => getAppPath()).toThrow(/mode/i);
    });
});

describe("appAsset", () => {
    it("prefixes public-root asset paths with the app path", () => {
        setBootstrap("path", APP);
        expect(appAsset("/kmza-icon.png")).toBe(`${APP}/kmza-icon.png`);
    });

    it("returns the path unchanged in port mode", () => {
        setBootstrap("port", "");
        expect(appAsset("/kmza-icon.png")).toBe("/kmza-icon.png");
    });
});

describe("appFetch", () => {
    it("prefixes same-app string inputs", async () => {
        setBootstrap("path", APP);
        const fetchSpy = vi
            .spyOn(globalThis, "fetch")
            .mockResolvedValue(new Response("{}"));
        await appFetch("/api/things");
        expect(fetchSpy).toHaveBeenCalledWith(`${APP}/api/things`, undefined);
    });

    it("prefixes same-origin URL inputs and preserves query strings", async () => {
        setBootstrap("path", APP);
        const fetchSpy = vi
            .spyOn(globalThis, "fetch")
            .mockResolvedValue(new Response("{}"));
        await appFetch(new URL(`${window.location.origin}/api/things?x=1`));
        const requested = fetchSpy.mock.calls[0][0] as URL;
        expect(requested).toBeInstanceOf(URL);
        expect(requested.pathname).toBe(`${APP}/api/things`);
        expect(requested.search).toBe("?x=1");
    });

    it("leaves cross-origin URL objects untouched (S1)", async () => {
        setBootstrap("path", APP);
        const fetchSpy = vi
            .spyOn(globalThis, "fetch")
            .mockResolvedValue(new Response("{}"));
        const external = new URL("https://external.example/api?x=1");
        await appFetch(external);
        const requested = fetchSpy.mock.calls[0][0] as URL;
        expect(requested.href).toBe("https://external.example/api?x=1");

        // Same host, different port is a different origin.
        const otherPort = new URL(`http://${window.location.hostname}:59999/api`);
        await appFetch(otherPort);
        const second = fetchSpy.mock.calls[1][0] as URL;
        expect(second.pathname).toBe("/api");
    });

    it("leaves absolute external URLs and Request objects untouched", async () => {
        setBootstrap("path", APP);
        const fetchSpy = vi
            .spyOn(globalThis, "fetch")
            .mockResolvedValue(new Response("{}"));

        await appFetch("https://external.example/api");
        expect(fetchSpy).toHaveBeenLastCalledWith("https://external.example/api", undefined);

        const request = new Request("http://localhost/api/raw");
        await appFetch(request);
        expect(fetchSpy).toHaveBeenLastCalledWith(request, undefined);
    });

    it("does not double-prefix an already prefixed path", async () => {
        setBootstrap("path", APP);
        const fetchSpy = vi
            .spyOn(globalThis, "fetch")
            .mockResolvedValue(new Response("{}"));
        await appFetch(`${APP}/api/things`);
        expect(fetchSpy).toHaveBeenCalledWith(`${APP}/api/things`, undefined);
    });
});

describe("loadKamiwazaRuntime", () => {
    it("fetches the runtime JSON from the prefixed route and memoizes it", async () => {
        setBootstrap("path", APP);
        const payload = {
            routingMode: "path",
            appPath: APP,
            appPathUrl: `https://host.example${APP}`,
            appUrl: `https://host.example${APP}`,
            deploymentId: "550e8400",
            appPort: "3000",
        };
        const fetchSpy = vi
            .spyOn(globalThis, "fetch")
            .mockResolvedValue(new Response(JSON.stringify(payload)));

        const first = await loadKamiwazaRuntime();
        const second = await loadKamiwazaRuntime();

        expect(first).toEqual(payload);
        expect(second).toBe(first);
        expect(fetchSpy).toHaveBeenCalledTimes(1);
        expect(fetchSpy).toHaveBeenCalledWith(
            `${APP}/kamiwaza/runtime.json`,
            expect.objectContaining({ cache: "no-store" }),
        );
    });

    it("does not memoize a failed fetch", async () => {
        setBootstrap("port", "");
        const fetchSpy = vi
            .spyOn(globalThis, "fetch")
            .mockResolvedValueOnce(new Response("nope", { status: 500 }))
            .mockResolvedValueOnce(
                new Response(
                    JSON.stringify({
                        routingMode: "port",
                        appPath: "",
                        appPathUrl: "",
                        appUrl: "",
                        deploymentId: "",
                        appPort: "",
                    }),
                ),
            );

        await expect(loadKamiwazaRuntime()).rejects.toThrow(/500/);
        const runtime = await loadKamiwazaRuntime();
        expect(runtime.routingMode).toBe("port");
        expect(fetchSpy).toHaveBeenCalledTimes(2);
    });
});
