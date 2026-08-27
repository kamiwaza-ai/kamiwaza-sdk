/**
 * Parity test: the TypeScript runtime-path implementation consumes the same
 * canonical vectors as Python's tests/unit/extensions_lib/test_runtime.py.
 * Keys in the vectors are snake_case (canonical); this file projects them.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { normalizeAppPath, withAppPath } from "../src/runtime/shared";
import { getKamiwazaRuntimeServer } from "../src/runtime/server";
import { resolveRoutingMode as resolveBootRouting } from "../scripts/start-next-runtime.mjs";

const VECTORS = JSON.parse(
    readFileSync(
        path.join(__dirname, "../../docs/extensions/runtime-path/routing-vectors.json"),
        "utf8",
    ),
);

describe("normalizeAppPath parity vectors", () => {
    for (const vector of VECTORS.normalize) {
        it(vector.name, () => {
            if (vector.expect_error) {
                expect(() => normalizeAppPath(vector.value)).toThrow();
            } else {
                expect(normalizeAppPath(vector.value)).toBe(vector.expect);
            }
        });
    }
});

describe("withAppPath parity vectors", () => {
    for (const vector of VECTORS.with_app_path) {
        it(vector.name, () => {
            expect(withAppPath(vector.path, vector.app_path)).toBe(vector.expect);
        });
    }
});

describe("runtime routing parity vectors", () => {
    for (const vector of VECTORS.routing) {
        it(vector.name, () => {
            if (vector.expect_error) {
                expect(() =>
                    getKamiwazaRuntimeServer(vector.env as NodeJS.ProcessEnv),
                ).toThrow();
                expect(() => resolveBootRouting(vector.env)).toThrow();
                return;
            }
            const runtime = getKamiwazaRuntimeServer(vector.env as NodeJS.ProcessEnv);
            const boot = resolveBootRouting(vector.env);
            expect(runtime.routingMode).toBe(vector.expect.routing_mode);
            expect(runtime.appPath).toBe(vector.expect.app_path);
            expect(boot.routingMode).toBe(vector.expect.routing_mode);
            expect(boot.appPath).toBe(vector.expect.app_path);
            expect(runtime.appPathUrl).toBe(vector.expect.app_path_url);
            expect(runtime.appUrl).toBe(vector.expect.app_url);
            expect(runtime.deploymentId).toBe(vector.expect.deployment_id);
            expect(runtime.appPort).toBe(vector.expect.app_port);
        });
    }
});
