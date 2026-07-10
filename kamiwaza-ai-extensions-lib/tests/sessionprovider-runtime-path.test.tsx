import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { render, waitFor } from "@testing-library/react";

import { SessionProvider } from "../src/client/SessionProvider";

const APP = "/runtime/apps/550e8400";

let fetchSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
        new Response(JSON.stringify({ authenticated: false }), { status: 200 }),
    );
});

afterEach(() => {
    delete (globalThis as Record<string, unknown>).__KAMIWAZA_RUNTIME__;
    vi.restoreAllMocks();
});

describe("SessionProvider runtime base path default", () => {
    it("uses the runtime bootstrap global when no basePath prop is given", async () => {
        (globalThis as Record<string, unknown>).__KAMIWAZA_RUNTIME__ = Object.freeze({
            routingMode: "path",
            appPath: APP,
        });
        render(
            <SessionProvider>
                <div />
            </SessionProvider>,
        );
        await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
        expect(String(fetchSpy.mock.calls[0][0])).toBe(`${APP}/session`);
    });

    it("lets an explicit basePath prop override the bootstrap global", async () => {
        (globalThis as Record<string, unknown>).__KAMIWAZA_RUNTIME__ = Object.freeze({
            routingMode: "path",
            appPath: APP,
        });
        render(
            <SessionProvider basePath="/explicit">
                <div />
            </SessionProvider>,
        );
        await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
        expect(String(fetchSpy.mock.calls[0][0])).toBe("/explicit/session");
    });

    it("falls back to the root when nothing is configured", async () => {
        render(
            <SessionProvider>
                <div />
            </SessionProvider>,
        );
        await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
        expect(String(fetchSpy.mock.calls[0][0])).toBe("/session");
    });
});
