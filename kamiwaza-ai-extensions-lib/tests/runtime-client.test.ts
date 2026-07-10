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
        await appFetch(new URL("http://localhost/api/things?x=1"));
        const requested = fetchSpy.mock.calls[0][0] as URL;
        expect(requested).toBeInstanceOf(URL);
        expect(requested.pathname).toBe(`${APP}/api/things`);
        expect(requested.search).toBe("?x=1");
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
            `${APP}/__kamiwaza/runtime.json`,
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
