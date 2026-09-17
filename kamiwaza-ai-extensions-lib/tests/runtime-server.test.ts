import { describe, expect, it } from "vitest";

import {
    createRuntimeConfigResponse,
    getKamiwazaRuntimeServer,
} from "../src/runtime/server";

const PATH_ENV = {
    KAMIWAZA_ROUTING_MODE: "path",
    KAMIWAZA_APP_PATH: "/runtime/apps/550e8400/",
    KAMIWAZA_APP_PATH_URL: "https://host.example/runtime/apps/550e8400/",
    KAMIWAZA_APP_URL: "https://host.example:8443/",
    KAMIWAZA_DEPLOYMENT_ID: "550e8400",
    KAMIWAZA_APP_PORT: "3000",
} as NodeJS.ProcessEnv;

describe("getKamiwazaRuntimeServer", () => {
    it("resolves path mode with APP_PATH_URL outranking APP_URL", () => {
        const runtime = getKamiwazaRuntimeServer(PATH_ENV);
        expect(runtime).toEqual({
            routingMode: "path",
            appPath: "/runtime/apps/550e8400",
            appPathUrl: "https://host.example/runtime/apps/550e8400",
            appUrl: "https://host.example/runtime/apps/550e8400",
            deploymentId: "550e8400",
            appPort: "3000",
        });
    });

    it("derives origin+appPath when APP_PATH_URL is unset", () => {
        const noPathUrl = { ...PATH_ENV, KAMIWAZA_APP_PATH_URL: "" };
        expect(getKamiwazaRuntimeServer(noPathUrl).appUrl).toBe(
            "https://host.example:8443/runtime/apps/550e8400",
        );

        const originOnly = {
            ...PATH_ENV,
            KAMIWAZA_APP_PATH_URL: "",
            KAMIWAZA_APP_URL: "",
            KAMIWAZA_ORIGIN: "https://origin.example",
        };
        expect(getKamiwazaRuntimeServer(originOnly).appUrl).toBe(
            "https://origin.example/runtime/apps/550e8400",
        );
    });

    it("resolves port mode with an empty app path even when a stale path is set", () => {
        const runtime = getKamiwazaRuntimeServer({
            KAMIWAZA_ROUTING_MODE: "port",
            KAMIWAZA_APP_PATH: "/runtime/apps/stale",
            KAMIWAZA_APP_URL: "https://host.example:8443/",
        } as NodeJS.ProcessEnv);
        expect(runtime.routingMode).toBe("port");
        expect(runtime.appPath).toBe("");
        expect(runtime.appPathUrl).toBe("");
        expect(runtime.appUrl).toBe("https://host.example:8443");
    });

    it("infers path mode from a nonempty app path when mode is unset", () => {
        const runtime = getKamiwazaRuntimeServer({
            KAMIWAZA_APP_PATH: "/runtime/apps/x",
        } as NodeJS.ProcessEnv);
        expect(runtime.routingMode).toBe("path");
        expect(runtime.appPath).toBe("/runtime/apps/x");
    });

    it("infers port mode when neither mode nor path is set", () => {
        const runtime = getKamiwazaRuntimeServer({} as NodeJS.ProcessEnv);
        expect(runtime.routingMode).toBe("port");
        expect(runtime.appPath).toBe("");
    });

    it("fails closed on explicit path mode without a usable path", () => {
        expect(() =>
            getKamiwazaRuntimeServer({ KAMIWAZA_ROUTING_MODE: "path" } as NodeJS.ProcessEnv),
        ).toThrow(/path/i);
    });

    it("fails closed on an invalid app path", () => {
        expect(() =>
            getKamiwazaRuntimeServer({
                KAMIWAZA_ROUTING_MODE: "path",
                KAMIWAZA_APP_PATH: "/runtime/../etc",
            } as NodeJS.ProcessEnv),
        ).toThrow(/invalid/i);
    });

    it("reports malformed public origins with the routing-contract diagnostic", () => {
        expect(() =>
            getKamiwazaRuntimeServer({
                KAMIWAZA_ROUTING_MODE: "path",
                KAMIWAZA_APP_PATH: "/runtime/apps/x",
                KAMIWAZA_APP_URL: "host.example",
            } as NodeJS.ProcessEnv),
        ).toThrow(/invalid public app URL for path routing/i);
    });

    it.each([
        "https://@host.example/runtime/apps/x",
        "https://:@host.example/runtime/apps/x",
        "\thttps://@host.example/runtime/apps/x",
    ])("rejects empty userinfo in the public path URL: %s", (appPathUrl) => {
        expect(() =>
            getKamiwazaRuntimeServer({
                KAMIWAZA_ROUTING_MODE: "path",
                KAMIWAZA_APP_PATH: "/runtime/apps/x",
                KAMIWAZA_APP_PATH_URL: appPathUrl,
            } as NodeJS.ProcessEnv),
        ).toThrow(/must be the public origin plus/i);
    });

    it("ignores a mismatched forwarded prefix (env is authoritative)", () => {
        const runtime = getKamiwazaRuntimeServer(PATH_ENV, "/runtime/apps/other");
        expect(runtime.appPath).toBe("/runtime/apps/550e8400");
    });
});

describe("createRuntimeConfigResponse", () => {
    it("returns a no-store JSON response with the runtime config", async () => {
        const response = createRuntimeConfigResponse(PATH_ENV);
        expect(response.headers.get("cache-control")).toBe("no-store");
        expect(response.headers.get("content-type")).toContain("application/json");
        const body = await response.json();
        expect(body.appPath).toBe("/runtime/apps/550e8400");
        expect(body.routingMode).toBe("path");
        expect(body.deploymentId).toBe("550e8400");
    });
});
