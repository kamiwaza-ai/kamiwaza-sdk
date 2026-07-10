/** Edge-scan fixes (codex-edge-1.md): B1, B2, S3, S4, S6, S7, S8, N3. */
import {
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
    computeSignalExitCode,
    prepareRuntime,
    transformRscBuffer,
    validateRuntimePath,
} from "../scripts/start-next-runtime.mjs";

const SENTINEL = "/__KZ_RUNTIME_BASE_7F3A91C2__";
const REAL = "/runtime/apps/550e8400-e29b-41d4-a716-446655440000";
const SHORT = "/r1";

let sourceRoot: string;
let targetRoot: string;

function write(rel: string, content: string | Buffer): void {
    const target = path.join(sourceRoot, rel);
    mkdirSync(path.dirname(target), { recursive: true });
    writeFileSync(target, content);
}

function writeFixture(): void {
    write("server.js", `const basePath = "${SENTINEL}";\n`);
    write(
        ".next/required-server-files.json",
        JSON.stringify({ config: { basePath: SENTINEL } }),
    );
    write(".next/routes-manifest.json", JSON.stringify({ basePath: SENTINEL }));
    write(".next/static/chunks/main-abc.js", `p="${SENTINEL}/_next/";`);
    write("node_modules/next/dist/server.js", "module.exports = {};");
    write("public/icon.png", Buffer.from([0x89, 0x50]));
}

async function manifestFor(): Promise<any> {
    return buildRelocationManifest({ root: sourceRoot, sentinel: SENTINEL, nextVersion: "15.5.19" });
}

beforeEach(() => {
    sourceRoot = mkdtempSync(path.join(tmpdir(), "kz-edge-src-"));
    targetRoot = path.join(mkdtempSync(path.join(tmpdir(), "kz-edge-dst-")), "runtime");
    writeFixture();
});

afterEach(() => {
    rmSync(sourceRoot, { recursive: true, force: true });
    rmSync(path.dirname(targetRoot), { recursive: true, force: true });
});

describe("transformRscBuffer (B1)", () => {
    it("rewrites T-frame byte lengths when the replacement is longer", () => {
        const payload = `${"x".repeat(1100)}${SENTINEL}`;
        const payloadBytes = Buffer.byteLength(payload);
        const input = Buffer.from(
            `0:{"p":"${SENTINEL}/x"}\n4:T${payloadBytes.toString(16)},${payload}5:["tail"]\n`,
        );
        const output = transformRscBuffer(input, SENTINEL, REAL);
        const text = output.toString("utf8");

        expect(text).not.toContain(SENTINEL);
        expect(text).toContain(`0:{"p":"${REAL}/x"}\n`);
        const newPayload = `${"x".repeat(1100)}${REAL}`;
        const newLen = Buffer.byteLength(newPayload).toString(16);
        expect(text).toContain(`4:T${newLen},${newPayload}`);
        expect(text.endsWith(`5:["tail"]\n`)).toBe(true);
    });

    it("rewrites T-frame byte lengths when the replacement is shorter", () => {
        const payload = `${SENTINEL}-payload`;
        const input = Buffer.from(
            `a:T${Buffer.byteLength(payload).toString(16)},${payload}b:1\n`,
        );
        const output = transformRscBuffer(input, SENTINEL, SHORT).toString("utf8");
        const newPayload = `${SHORT}-payload`;
        expect(output).toContain(`a:T${Buffer.byteLength(newPayload).toString(16)},${newPayload}`);
        expect(output.endsWith("b:1\n")).toBe(true);
    });

    it("fails closed on a sentinel inside an unknown length-framed row", () => {
        const payload = Buffer.concat([Buffer.from([1, 2, 3]), Buffer.from(SENTINEL)]);
        const input = Buffer.concat([
            Buffer.from(`7:B${payload.length.toString(16)},`),
            payload,
        ]);
        expect(() => transformRscBuffer(input, SENTINEL, REAL)).toThrow(/length-framed|frame/i);
    });

    it("passes through sentinel-free content unchanged", () => {
        const input = Buffer.from(`0:{"a":1}\n4:T3,abc5:2\n`);
        expect(transformRscBuffer(input, SENTINEL, REAL).equals(input)).toBe(true);
    });

    it("handles empty-id hint rows (:HL) as emitted by real Next 15.5.19 output", () => {
        const input = Buffer.from(
            `:HL["${SENTINEL}/_next/static/css/x.css","style"]\n0:{"p":"${SENTINEL}/y"}\n`,
        );
        const output = transformRscBuffer(input, SENTINEL, REAL).toString("utf8");
        expect(output).toBe(
            `:HL["${REAL}/_next/static/css/x.css","style"]\n0:{"p":"${REAL}/y"}\n`,
        );
    });
});

describe("prepareRuntime edge fixes", () => {
    it("relocates .rsc files with framing awareness end-to-end (B1)", async () => {
        const payload = `hello ${SENTINEL} world`;
        write(
            ".next/server/app/index.rsc",
            `1:{"base":"${SENTINEL}"}\n2:T${Buffer.byteLength(payload).toString(16)},${payload}`,
        );
        const manifest = await manifestFor();
        await prepareRuntime({ sourceRoot, targetRoot, manifest, replacement: REAL });
        const out = readFileSync(path.join(targetRoot, ".next/server/app/index.rsc"), "utf8");
        const newPayload = `hello ${REAL} world`;
        expect(out).toBe(
            `1:{"base":"${REAL}"}\n2:T${Buffer.byteLength(newPayload).toString(16)},${newPayload}`,
        );
    });

    it("preserves non-sentinel bytes exactly, including invalid UTF-8 (S3)", async () => {
        write(
            ".next/server/raw.txt",
            Buffer.concat([
                Buffer.from([0xff, 0xfe]),
                Buffer.from(SENTINEL),
                Buffer.from([0x80]),
            ]),
        );
        const manifest = await manifestFor();
        await prepareRuntime({ sourceRoot, targetRoot, manifest, replacement: REAL });
        const out = readFileSync(path.join(targetRoot, ".next/server/raw.txt"));
        expect(
            out.equals(
                Buffer.concat([
                    Buffer.from([0xff, 0xfe]),
                    Buffer.from(REAL),
                    Buffer.from([0x80]),
                ]),
            ),
        ).toBe(true);
    });

    it("fails closed when the manifest lists a file the mirror cannot patch (B2)", async () => {
        const manifest = await manifestFor();
        manifest.files.push({
            path: "public/config.txt",
            size: 10,
            sha256: "0".repeat(64),
            occurrences: 1,
            kind: "txt",
        });
        await expect(
            prepareRuntime({ sourceRoot, targetRoot, manifest, replacement: REAL }),
        ).rejects.toThrow(/public|patch/i);
    });

    it.each([
        ["wrong schema", (m: any) => (m.schemaVersion = 2)],
        ["empty sentinel", (m: any) => (m.sentinel = "")],
        ["empty files", (m: any) => (m.files = [])],
        ["duplicate paths", (m: any) => m.files.push({ ...m.files[0] })],
        [
            "traversal path",
            (m: any) => m.files.push({ ...m.files[0], path: "../escape.js" }),
        ],
        ["unknown kind", (m: any) => (m.files[0].kind = "wasm")],
        ["bad sha", (m: any) => (m.files[0].sha256 = "zz")],
        ["negative size", (m: any) => (m.files[0].size = -1)],
    ])("rejects a malformed manifest: %s (S4)", async (_name, mutate) => {
        const manifest = await manifestFor();
        mutate(manifest);
        await expect(
            prepareRuntime({ sourceRoot, targetRoot, manifest, replacement: REAL }),
        ).rejects.toThrow();
    });

    it("rejects relative or overlapping roots (S7)", async () => {
        const manifest = await manifestFor();
        await expect(
            prepareRuntime({
                sourceRoot: "relative/source",
                targetRoot,
                manifest,
                replacement: REAL,
            }),
        ).rejects.toThrow(/absolute/i);
        await expect(
            prepareRuntime({
                sourceRoot,
                targetRoot: path.join(sourceRoot, "nested"),
                manifest,
                replacement: REAL,
            }),
        ).rejects.toThrow(/disjoint|overlap|inside/i);
        await expect(
            prepareRuntime({ sourceRoot, targetRoot: sourceRoot, manifest, replacement: REAL }),
        ).rejects.toThrow(/disjoint|overlap|same/i);
    });

    it("second concurrent start fails deterministically without touching the live tree (S8)", async () => {
        const manifest = await manifestFor();
        await prepareRuntime({ sourceRoot, targetRoot, manifest, replacement: REAL });
        const before = readFileSync(path.join(targetRoot, "server.js"), "utf8");

        await expect(
            prepareRuntime({ sourceRoot, targetRoot, manifest, replacement: REAL }),
        ).rejects.toThrow(/lock|another/i);
        expect(readFileSync(path.join(targetRoot, "server.js"), "utf8")).toBe(before);
    });

    it("steals a stale lock from a dead process (S8)", async () => {
        const manifest = await manifestFor();
        mkdirSync(`${targetRoot}.lock`, { recursive: true });
        writeFileSync(path.join(`${targetRoot}.lock`, "pid"), "4194000");
        await prepareRuntime({ sourceRoot, targetRoot, manifest, replacement: REAL });
        expect(readFileSync(path.join(targetRoot, "server.js"), "utf8")).toContain(REAL);
    });

    it("fails when the artifact's traced Next version disagrees with the manifest (B5)", async () => {
        write(
            "node_modules/next/package.json",
            JSON.stringify({ name: "next", version: "15.4.0" }),
        );
        const manifest = await manifestFor();
        await expect(
            prepareRuntime({ sourceRoot, targetRoot, manifest, replacement: REAL }),
        ).rejects.toThrow(/15\.4\.0|version/i);
    });
});

describe("validateRuntimePath hardening (S6)", () => {
    it("rejects raw control characters before trimming", () => {
        expect(() => validateRuntimePath("/runtime/apps/x\n")).toThrow(/control/i);
        expect(() => validateRuntimePath("/runtime/apps/x\t")).toThrow(/control/i);
        expect(() => validateRuntimePath("\r/runtime/apps/x")).toThrow(/control/i);
    });

    it("bounds total and segment length", () => {
        expect(() => validateRuntimePath(`/a/${"b".repeat(600)}`)).toThrow(/length|long/i);
        expect(validateRuntimePath(`/runtime/apps/${"c".repeat(120)}`)).toBeTruthy();
    });

    it("rejects any path containing a sentinel-family substring", () => {
        expect(() =>
            validateRuntimePath("/runtime/apps/__KZ_RUNTIME_BASE_7F3A91C2__x"),
        ).toThrow(/sentinel/i);
    });
});

describe("computeSignalExitCode (N3)", () => {
    it("maps signals to 128+N", () => {
        expect(computeSignalExitCode("SIGTERM")).toBe(143);
        expect(computeSignalExitCode("SIGKILL")).toBe(137);
        expect(computeSignalExitCode("SIGINT")).toBe(130);
        expect(computeSignalExitCode("SIGNOTREAL" as never)).toBe(1);
    });
});
