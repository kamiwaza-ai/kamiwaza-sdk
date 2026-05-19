import { describe, it, expect, vi } from "vitest";
import { fetchModels } from "../src/server/models";

describe("fetchModels", () => {
    it("parses standard model fields", async () => {
        const mockData = [
            { id: "dep-1", name: "llama-3", status: "running", type: "chat" },
            { id: "dep-2", name: "gpt-4", status: "starting" },
        ];

        vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
            ok: true,
            json: async () => mockData,
        }));

        const models = await fetchModels("http://backend:8000");

        expect(models).toHaveLength(2);
        expect(models[0].id).toBe("dep-1");
        expect(models[0].name).toBe("llama-3");
        expect(models[0].status).toBe("running");
        expect(models[1].name).toBe("gpt-4");

        vi.unstubAllGlobals();
    });

    it("handles deployment format fields", async () => {
        const mockData = [
            { deployment_id: "dep-1", model_name: "llama", phase: "Running" },
        ];

        vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
            ok: true,
            json: async () => mockData,
        }));

        const models = await fetchModels("http://backend:8000");

        expect(models[0].id).toBe("dep-1");
        expect(models[0].name).toBe("llama");
        expect(models[0].status).toBe("Running");

        vi.unstubAllGlobals();
    });

    it("returns empty array on error", async () => {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
            ok: false,
            status: 500,
        }));

        const models = await fetchModels("http://backend:8000");
        expect(models).toEqual([]);

        vi.unstubAllGlobals();
    });

    it("returns empty array for non-array response", async () => {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({ error: "not found" }),
        }));

        const models = await fetchModels("http://backend:8000");
        expect(models).toEqual([]);

        vi.unstubAllGlobals();
    });
});
