import { mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
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
            nextVersion: "15.5.24",
        });

        expect(manifest.schemaVersion).toBe(1);
        expect(manifest.nextVersion).toBe("15.5.24");
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
            nextVersion: "15.5.24",
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
            ".next/server/poisoned.png",
            Buffer.concat([Buffer.from([0x89, 0x50, 0x00]), Buffer.from(SENTINEL)]),
        );
        await expect(
            buildRelocationManifest({ root, sentinel: SENTINEL, nextVersion: "15.5.24" }),
        ).rejects.toThrow(/binary|unrecognized/i);
    });

    it("indexes percent-encoded relocation sentinels emitted in URL parameters", async () => {
        writeStandardFixture();
        write(
            ".next/server/encoded.js",
            `const upper = "${encodeURIComponent(SENTINEL)}"; ` +
                `const lower = "${encodeURIComponent(SENTINEL).replaceAll("%2F", "%2f")}";`,
        );
        const manifest = await buildRelocationManifest({
            root,
            sentinel: SENTINEL,
            nextVersion: "15.5.24",
        });
        expect(
            manifest.files.find((file) => file.path === ".next/server/encoded.js")
                ?.occurrences,
        ).toBe(2);
    });

    it("fails on an unsupported slash-less relocation sentinel family", async () => {
        writeStandardFixture();
        write(".next/server/noncanonical.js", `const bare = "${SENTINEL.slice(1)}";`);
        await expect(
            buildRelocationManifest({ root, sentinel: SENTINEL, nextVersion: "15.5.24" }),
        ).rejects.toThrow(/non-canonical relocation sentinel/i);
    });

    it("fails when the sentinel appears in node_modules", async () => {
        writeStandardFixture();
        write("node_modules/evil/index.js", `const p = "${SENTINEL}";`);
        await expect(
            buildRelocationManifest({ root, sentinel: SENTINEL, nextVersion: "15.5.24" }),
        ).rejects.toThrow(/node_modules/i);
    });

    it("fails when production source maps contain the sentinel", async () => {
        writeStandardFixture();
        write(".next/static/chunks/main-abc123.js.map", `{"x":"${SENTINEL}"}`);
        await expect(
            buildRelocationManifest({ root, sentinel: SENTINEL, nextVersion: "15.5.24" }),
        ).rejects.toThrow(/source map/i);
    });

    it("fails when a .next/cache directory is present", async () => {
        writeStandardFixture();
        write(".next/cache/webpack/0.pack", "cache");
        await expect(
            buildRelocationManifest({ root, sentinel: SENTINEL, nextVersion: "15.5.24" }),
        ).rejects.toThrow(/cache/i);
    });

    it("classifies redirect .meta output as JSON and indexes it (B4)", async () => {
        writeStandardFixture();
        write(
            ".next/server/app/go.meta",
            JSON.stringify({ status: 307, headers: { location: `${SENTINEL}/other` } }),
        );
        const manifest = await buildRelocationManifest({
            root,
            sentinel: SENTINEL,
            nextVersion: "15.5.24",
        });
        const entry = manifest.files.find((f) => f.path === ".next/server/app/go.meta");
        expect(entry?.kind).toBe("json");
        expect(entry?.occurrences).toBe(1);
    });

    it("rejects any source map under the artifact even without the sentinel (S5)", async () => {
        writeStandardFixture();
        write(".next/static/chunks/clean.js.map", `{"version":3,"mappings":"AAAA"}`);
        await expect(
            buildRelocationManifest({ root, sentinel: SENTINEL, nextVersion: "15.5.24" }),
        ).rejects.toThrow(/source map/i);
    });

    it("allows dependency-shipped source maps inside node_modules (S5 scope)", async () => {
        writeStandardFixture();
        write("node_modules/somepkg/dist/index.js.map", `{"version":3}`);
        const manifest = await buildRelocationManifest({
            root,
            sentinel: SENTINEL,
            nextVersion: "15.5.24",
        });
        expect(manifest.files.length).toBeGreaterThan(0);
    });

    it("allows source maps under public because public assets are never relocated", async () => {
        writeStandardFixture();
        write("public/vendor/library.js.map", `{"version":3,"mappings":"AAAA"}`);
        const manifest = await buildRelocationManifest({
            root,
            sentinel: SENTINEL,
            nextVersion: "15.5.24",
        });
        expect(manifest.files.some((file) => file.path.startsWith("public/"))).toBe(
            false,
        );
    });

    it("rejects source maps linked into the runtime artifact", async () => {
        writeStandardFixture();
        write(".hidden-clean-map", `{"version":3}`);
        symlinkSync(
            path.join(root, ".hidden-clean-map"),
            path.join(root, ".next/static/chunks/linked.js.map"),
        );
        await expect(
            buildRelocationManifest({ root, sentinel: SENTINEL, nextVersion: "15.5.24" }),
        ).rejects.toThrow(/source map/i);
    });

    it("rejects sentinel occurrences under public/ (B2)", async () => {
        writeStandardFixture();
        write("public/config.txt", `base=${SENTINEL}`);
        await expect(
            buildRelocationManifest({ root, sentinel: SENTINEL, nextVersion: "15.5.24" }),
        ).rejects.toThrow(/public/i);
    });

    it("rejects a sentinel hidden behind a file symlink (B3)", async () => {
        writeStandardFixture();
        write(".hidden-target.js", `const p = "${SENTINEL}";`);
        symlinkSync(
            path.join(root, ".hidden-target.js"),
            path.join(root, ".next/server/linked.js"),
        );
        await expect(
            buildRelocationManifest({ root, sentinel: SENTINEL, nextVersion: "15.5.24" }),
        ).rejects.toThrow(/symlink/i);
    });

    it("rejects broken and cyclic symlinks (B3)", async () => {
        writeStandardFixture();
        symlinkSync(path.join(root, "does-not-exist"), path.join(root, ".next/broken"));
        await expect(
            buildRelocationManifest({ root, sentinel: SENTINEL, nextVersion: "15.5.24" }),
        ).rejects.toThrow(/symlink/i);

        rmSync(path.join(root, ".next/broken"));
        symlinkSync(path.join(root, ".next/loop"), path.join(root, ".next/loop"));
        await expect(
            buildRelocationManifest({ root, sentinel: SENTINEL, nextVersion: "15.5.24" }),
        ).rejects.toThrow(/symlink/i);
    });

    it("rejects symlinks escaping the artifact root (B3)", async () => {
        writeStandardFixture();
        symlinkSync("/etc/hosts", path.join(root, ".next/escape"));
        await expect(
            buildRelocationManifest({ root, sentinel: SENTINEL, nextVersion: "15.5.24" }),
        ).rejects.toThrow(/symlink|escape/i);
    });

    it("indexes a textual .body via its sibling .meta content type (S3)", async () => {
        writeStandardFixture();
        write(".next/server/app/feed.body", `{"base":"${SENTINEL}"}`);
        write(
            ".next/server/app/feed.meta",
            JSON.stringify({ status: 200, headers: { "content-type": "application/json" } }),
        );
        const manifest = await buildRelocationManifest({
            root,
            sentinel: SENTINEL,
            nextVersion: "15.5.24",
        });
        const entry = manifest.files.find((f) => f.path === ".next/server/app/feed.body");
        expect(entry?.occurrences).toBe(1);
    });

    it("classifies an HTML .body for Flight-length-aware relocation", async () => {
        writeStandardFixture();
        const flightLength = Buffer.byteLength(SENTINEL).toString(16);
        write(
            ".next/server/app/cached-page.body",
            `<script>self.__next_f.push([1,"1:T${flightLength},${SENTINEL}"])</script>`,
        );
        write(
            ".next/server/app/cached-page.meta",
            JSON.stringify({
                status: 200,
                headers: { "content-type": "text/html; charset=utf-8" },
            }),
        );

        const manifest = await buildRelocationManifest({
            root,
            sentinel: SENTINEL,
            nextVersion: "15.5.24",
        });

        const entry = manifest.files.find(
            (file) => file.path === ".next/server/app/cached-page.body",
        );
        expect(entry?.kind).toBe("html");
        expect(entry?.occurrences).toBe(1);
    });

    it("classifies a text/x-component .body as React Flight data", async () => {
        writeStandardFixture();
        const flightLength = Buffer.byteLength(SENTINEL).toString(16);
        write(
            ".next/server/app/cached-flight.body",
            `1:T${flightLength},${SENTINEL}`,
        );
        write(
            ".next/server/app/cached-flight.meta",
            JSON.stringify({
                status: 200,
                headers: { "content-type": "text/x-component; charset=utf-8" },
            }),
        );

        const manifest = await buildRelocationManifest({
            root,
            sentinel: SENTINEL,
            nextVersion: "15.5.24",
        });

        const entry = manifest.files.find(
            (file) => file.path === ".next/server/app/cached-flight.body",
        );
        expect(entry?.kind).toBe("rsc");
        expect(entry?.occurrences).toBe(1);
    });

    it("rejects a sentinel inside a binary .body (S3)", async () => {
        writeStandardFixture();
        write(
            ".next/server/app/blob.body",
            Buffer.concat([Buffer.from([0x89, 0x50]), Buffer.from(SENTINEL)]),
        );
        write(
            ".next/server/app/blob.meta",
            JSON.stringify({ status: 200, headers: { "content-type": "image/png" } }),
        );
        await expect(
            buildRelocationManifest({ root, sentinel: SENTINEL, nextVersion: "15.5.24" }),
        ).rejects.toThrow(/body|binary/i);
    });

    it("rejects a relocatable .body with stale content-length metadata", async () => {
        writeStandardFixture();
        write(".next/server/app/feed.body", `{"base":"${SENTINEL}"}`);
        write(
            ".next/server/app/feed.meta",
            JSON.stringify({
                status: 200,
                headers: {
                    "Content-Type": "application/json",
                    "Content-Length": "48",
                },
            }),
        );
        await expect(
            buildRelocationManifest({ root, sentinel: SENTINEL, nextVersion: "15.5.24" }),
        ).rejects.toThrow(/content-length/i);
    });

    it("fails when mandatory roles carry no sentinel occurrences", async () => {
        // A path-variant build whose server config lost the sentinel is a
        // broken artifact: fail the image build, not the deployment.
        write("server.js", "const basePath = '';\n");
        write(".next/routes-manifest.json", JSON.stringify({ basePath: "" }));
        write(".next/static/chunks/main.js", "x()");
        await expect(
            buildRelocationManifest({ root, sentinel: SENTINEL, nextVersion: "15.5.24" }),
        ).rejects.toThrow(/mandatory/i);
    });
});
