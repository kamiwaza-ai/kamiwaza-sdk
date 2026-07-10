import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { buildRelocationManifest } from "../scripts/index-next-runtime.mjs";

const SENTINEL = "/__KZ_RUNTIME_BASE_7F3A91C2__";

let root: string;

function write(rel: string, content: string | Buffer): void {
    const target = path.join(root, rel);
    mkdirSync(path.dirname(target), { recursive: true });
    writeFileSync(target, content);
}

beforeEach(() => {
    root = mkdtempSync(path.join(tmpdir(), "kz-index-test-"));
});

afterEach(() => {
    rmSync(root, { recursive: true, force: true });
});

function writeStandardFixture(): void {
    write("server.js", `const basePath = "${SENTINEL}";\nrequire("./x");\n`);
    write(
        ".next/required-server-files.json",
        JSON.stringify({ config: { basePath: SENTINEL, assetPrefix: SENTINEL } }),
    );
    write(".next/routes-manifest.json", JSON.stringify({ basePath: SENTINEL }));
    write(
        ".next/static/chunks/main-abc123.js",
        `p="${SENTINEL}/_next/";x("${SENTINEL}/api")`,
    );
    write(".next/server/app/index.html", `<script src="${SENTINEL}/_next/x.js"></script>`);
    write(".next/static/css/app.css", `body{background:url(${SENTINEL}/_next/static/f.woff2)}`);
    write("public/kmza-icon.png", Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a]));
    write("node_modules/next/dist/server.js", "module.exports = {};\n");
}

describe("buildRelocationManifest", () => {
    it("indexes every sentinel-bearing runtime file with counts and hashes", async () => {
        writeStandardFixture();
        const manifest = await buildRelocationManifest({
            root,
            sentinel: SENTINEL,
            nextVersion: "15.5.19",
        });

        expect(manifest.schemaVersion).toBe(1);
        expect(manifest.nextVersion).toBe("15.5.19");
        expect(manifest.sentinel).toBe(SENTINEL);

        const byPath = Object.fromEntries(manifest.files.map((f) => [f.path, f]));
        expect(byPath["server.js"].occurrences).toBe(1);
        expect(byPath[".next/required-server-files.json"].occurrences).toBe(2);
        expect(byPath[".next/static/chunks/main-abc123.js"].occurrences).toBe(2);
        expect(byPath[".next/server/app/index.html"].occurrences).toBe(1);
        expect(byPath[".next/static/css/app.css"].occurrences).toBe(1);
        expect(byPath["public/kmza-icon.png"]).toBeUndefined();
        expect(manifest.files.every((f) => /^[0-9a-f]{64}$/.test(f.sha256))).toBe(true);
        expect(manifest.files.every((f) => f.size > 0)).toBe(true);
    });

    it("classifies files by kind", async () => {
        writeStandardFixture();
        const manifest = await buildRelocationManifest({
            root,
            sentinel: SENTINEL,
            nextVersion: "15.5.19",
        });
        const kinds = Object.fromEntries(manifest.files.map((f) => [f.path, f.kind]));
        expect(kinds["server.js"]).toBe("js");
        expect(kinds[".next/routes-manifest.json"]).toBe("json");
        expect(kinds[".next/server/app/index.html"]).toBe("html");
        expect(kinds[".next/static/css/app.css"]).toBe("css");
    });

    it("fails when the sentinel appears in a binary file", async () => {
        writeStandardFixture();
        write(
            "public/poisoned.png",
            Buffer.concat([Buffer.from([0x89, 0x50, 0x00]), Buffer.from(SENTINEL)]),
        );
        await expect(
            buildRelocationManifest({ root, sentinel: SENTINEL, nextVersion: "15.5.19" }),
        ).rejects.toThrow(/binary|unrecognized/i);
    });

    it("fails when the sentinel appears in node_modules", async () => {
        writeStandardFixture();
        write("node_modules/evil/index.js", `const p = "${SENTINEL}";`);
        await expect(
            buildRelocationManifest({ root, sentinel: SENTINEL, nextVersion: "15.5.19" }),
        ).rejects.toThrow(/node_modules/i);
    });

    it("fails when production source maps contain the sentinel", async () => {
        writeStandardFixture();
        write(".next/static/chunks/main-abc123.js.map", `{"x":"${SENTINEL}"}`);
        await expect(
            buildRelocationManifest({ root, sentinel: SENTINEL, nextVersion: "15.5.19" }),
        ).rejects.toThrow(/source map/i);
    });

    it("fails when a .next/cache directory is present", async () => {
        writeStandardFixture();
        write(".next/cache/webpack/0.pack", "cache");
        await expect(
            buildRelocationManifest({ root, sentinel: SENTINEL, nextVersion: "15.5.19" }),
        ).rejects.toThrow(/cache/i);
    });

    it("fails when mandatory roles carry no sentinel occurrences", async () => {
        // A path-variant build whose server config lost the sentinel is a
        // broken artifact: fail the image build, not the deployment.
        write("server.js", "const basePath = '';\n");
        write(".next/routes-manifest.json", JSON.stringify({ basePath: "" }));
        write(".next/static/chunks/main.js", "x()");
        await expect(
            buildRelocationManifest({ root, sentinel: SENTINEL, nextVersion: "15.5.19" }),
        ).rejects.toThrow(/mandatory/i);
    });
});
