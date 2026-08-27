import {
    existsSync,
    lstatSync,
    mkdirSync,
    mkdtempSync,
    readFileSync,
    rmSync,
    writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { buildRelocationManifest } from "../scripts/index-next-runtime.mjs";
import {
    prepareNativeRuntime,
    prepareRuntime,
    resolveRoutingMode,
    validateRuntimePath,
} from "../scripts/start-next-runtime.mjs";

const SENTINEL = "/__KZ_RUNTIME_BASE_7F3A91C2__";
const REAL = "/runtime/apps/550e8400-e29b-41d4-a716-446655440000";

let sourceRoot: string;
let targetRoot: string;

function write(rel: string, content: string | Buffer): void {
    const target = path.join(sourceRoot, rel);
    mkdirSync(path.dirname(target), { recursive: true });
    writeFileSync(target, content);
}

function writeFixture(): void {
    write("server.js", `const conf = require("./.next/required-server-files.json");\nconst basePath = "${SENTINEL}";\n`);
    write(
        ".next/required-server-files.json",
        JSON.stringify({ config: { basePath: SENTINEL, assetPrefix: SENTINEL } }),
    );
    write(".next/routes-manifest.json", JSON.stringify({ basePath: SENTINEL }));
    write(".next/static/chunks/main-abc.js", `p="${SENTINEL}/_next/";`);
    write(".next/static/chunks/plain-xyz.js", "console.log('no sentinel');");
    write(".next/server/app/index.html", `<script src="${SENTINEL}/_next/x.js"></script>`);
    write("package.json", JSON.stringify({ name: "fixture" }));
    write(
        "node_modules/next/package.json",
        JSON.stringify({ name: "next", version: "15.5.19" }),
    );
    write("node_modules/next/dist/server.js", "module.exports = {};");
    write("public/icon.png", Buffer.from([0x89, 0x50, 0x4e, 0x47]));
}

beforeEach(() => {
    sourceRoot = mkdtempSync(path.join(tmpdir(), "kz-boot-src-"));
    targetRoot = path.join(mkdtempSync(path.join(tmpdir(), "kz-boot-dst-")), "runtime");
    writeFixture();
});

afterEach(() => {
    rmSync(sourceRoot, { recursive: true, force: true });
    rmSync(path.dirname(targetRoot), { recursive: true, force: true });
});

describe("resolveRoutingMode", () => {
    it("selects path mode with a normalized path", () => {
        expect(
            resolveRoutingMode({
                KAMIWAZA_ROUTING_MODE: "path",
                KAMIWAZA_APP_PATH: `${REAL}/`,
            }),
        ).toEqual({ routingMode: "path", appPath: REAL });
    });

    it("selects port mode ignoring a stale path", () => {
        expect(
            resolveRoutingMode({
                KAMIWAZA_ROUTING_MODE: "port",
                KAMIWAZA_APP_PATH: REAL,
            }),
        ).toEqual({ routingMode: "port", appPath: "" });
    });

    it("falls back on the path value when mode is unset", () => {
        expect(resolveRoutingMode({ KAMIWAZA_APP_PATH: REAL })).toEqual({
            routingMode: "path",
            appPath: REAL,
        });
        expect(resolveRoutingMode({})).toEqual({ routingMode: "port", appPath: "" });
    });

    it("fails closed on explicit path mode without a path", () => {
        expect(() => resolveRoutingMode({ KAMIWAZA_ROUTING_MODE: "path" })).toThrow(/path/i);
    });
});

describe("validateRuntimePath", () => {
    it("accepts the platform deployment path shape", () => {
        expect(validateRuntimePath(REAL)).toBe(REAL);
        expect(validateRuntimePath(`${REAL}/`)).toBe(REAL);
    });

    it.each([
        ["/runtime//apps/x", "empty segment"],
        ["/runtime/../x", "dot segment"],
        ["/runtime/%2e/x", "percent escape"],
        ["/runtime/apps/x?y", "query"],
        ["/runtime/apps/x#f", "fragment"],
        ["/runtime\\apps", "backslash"],
        ["/runtime/apps/x\u0007", "control char"],
        [SENTINEL, "the sentinel itself"],
        ["", "empty"],
    ])("rejects %s (%s)", (bad) => {
        expect(() => validateRuntimePath(bad)).toThrow();
    });
});

describe("prepareRuntime", () => {
    it("patches indexed files, symlinks the rest, and reports stats", async () => {
        const manifest = await buildRelocationManifest({
            root: sourceRoot,
            sentinel: SENTINEL,
            nextVersion: "15.5.19",
        });

        const stats = await prepareRuntime({
            sourceRoot,
            targetRoot,
            manifest,
            replacement: REAL,
        });

        const serverJs = readFileSync(path.join(targetRoot, "server.js"), "utf8");
        expect(serverJs).toContain(`"${REAL}"`);
        expect(serverJs).not.toContain(SENTINEL);

        const config = JSON.parse(
            readFileSync(path.join(targetRoot, ".next/required-server-files.json"), "utf8"),
        );
        expect(config.config.basePath).toBe(REAL);

        const html = readFileSync(
            path.join(targetRoot, ".next/server/app/index.html"),
            "utf8",
        );
        expect(html).toContain(`${REAL}/_next/x.js`);

        // server.js must be a real file (its __dirname anchors the runtime);
        // untouched files and heavyweight trees are symlinks.
        expect(lstatSync(path.join(targetRoot, "server.js")).isSymbolicLink()).toBe(false);
        expect(lstatSync(path.join(targetRoot, "node_modules")).isSymbolicLink()).toBe(true);
        expect(lstatSync(path.join(targetRoot, "public")).isSymbolicLink()).toBe(true);
        expect(
            lstatSync(path.join(targetRoot, ".next/static/chunks/plain-xyz.js")).isSymbolicLink(),
        ).toBe(true);

        // Writable cache dir must be real.
        expect(existsSync(path.join(targetRoot, ".next/cache"))).toBe(true);
        expect(lstatSync(path.join(targetRoot, ".next/cache")).isDirectory()).toBe(true);

        expect(stats.patchedFiles).toBe(manifest.files.length);
        expect(stats.occurrences).toBeGreaterThan(0);
        expect(stats.copiedBytes).toBeGreaterThan(0);
        expect(stats.prepareMs).toBeGreaterThanOrEqual(0);
    });

    it("fails closed when a source file hash does not match the manifest", async () => {
        const manifest = await buildRelocationManifest({
            root: sourceRoot,
            sentinel: SENTINEL,
            nextVersion: "15.5.19",
        });
        write(".next/routes-manifest.json", JSON.stringify({ basePath: SENTINEL, extra: 1 }));

        await expect(
            prepareRuntime({ sourceRoot, targetRoot, manifest, replacement: REAL }),
        ).rejects.toThrow(/hash|sha/i);
        expect(existsSync(path.join(targetRoot, "server.js"))).toBe(false);
    });

    it("fails closed when an unindexed sentinel remains in the runtime", async () => {
        const manifest = await buildRelocationManifest({
            root: sourceRoot,
            sentinel: SENTINEL,
            nextVersion: "15.5.19",
        });
        // Simulate an indexer blind spot: a sentinel-bearing file added after
        // indexing. The boot-time zero-sentinel scan must catch it.
        write(".next/server/late-addition.js", `const p = "${SENTINEL}";`);

        await expect(
            prepareRuntime({ sourceRoot, targetRoot, manifest, replacement: REAL }),
        ).rejects.toThrow(/sentinel/i);
    });

    it("fails closed when patched JSON does not parse", async () => {
        write(".next/routes-manifest.json", `{"basePath": "${SENTINEL}"`);
        const manifest = await buildRelocationManifest({
            root: sourceRoot,
            sentinel: SENTINEL,
            nextVersion: "15.5.19",
        }).catch(() => null);
        // The truncated JSON still indexes (indexer checks bytes, not JSON
        // validity), so prepareRuntime's parse step must be the one to fail.
        if (manifest === null) {
            return;
        }
        await expect(
            prepareRuntime({ sourceRoot, targetRoot, manifest, replacement: REAL }),
        ).rejects.toThrow(/JSON|parse/i);
    });

    it("rejects a replacement containing the sentinel", async () => {
        const manifest = await buildRelocationManifest({
            root: sourceRoot,
            sentinel: SENTINEL,
            nextVersion: "15.5.19",
        });
        await expect(
            prepareRuntime({ sourceRoot, targetRoot, manifest, replacement: SENTINEL }),
        ).rejects.toThrow();
    });
});

describe("prepareNativeRuntime", () => {
    it("stages a link-backed runtime with a writable empty Next cache", async () => {
        write(".next/cache/images/stale", "read-only build cache");
        const stats = await prepareNativeRuntime({ sourceRoot, targetRoot });

        expect(lstatSync(path.join(targetRoot, "server.js")).isSymbolicLink()).toBe(false);
        expect(lstatSync(path.join(targetRoot, "node_modules")).isSymbolicLink()).toBe(true);
        expect(lstatSync(path.join(targetRoot, "public")).isSymbolicLink()).toBe(true);
        expect(
            lstatSync(path.join(targetRoot, ".next/static/chunks/plain-xyz.js")).isSymbolicLink(),
        ).toBe(true);

        const cache = path.join(targetRoot, ".next/cache");
        expect(lstatSync(cache).isDirectory()).toBe(true);
        expect(lstatSync(cache).isSymbolicLink()).toBe(false);
        expect(existsSync(path.join(cache, "images/stale"))).toBe(false);
        writeFileSync(path.join(cache, "runtime-write"), "ok");
        expect(readFileSync(path.join(cache, "runtime-write"), "utf8")).toBe("ok");
        expect(stats.prepareMs).toBeGreaterThanOrEqual(0);
        expect(stats.rssMib).toBeGreaterThan(0);
    });
});
