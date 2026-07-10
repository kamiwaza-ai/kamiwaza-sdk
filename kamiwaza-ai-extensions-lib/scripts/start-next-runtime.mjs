#!/usr/bin/env node
/**
 * Boot entrypoint for the App Garden dual-artifact Next runtime.
 *
 * Port mode: start the native no-base artifact as-is.
 * Path mode: build a sparse writable mirror of the sentinel artifact in a
 * staging directory — copy + byte-replace the indexed files (with
 * Flight-frame awareness for .rsc), symlink everything else — verify
 * fail-closed (manifest schema, hashes, counts, JSON parse, patched totals,
 * zero residual sentinel), then publish atomically and `node server.js`.
 *
 * Concurrency: a per-target lock directory (holding the owner pid) makes a
 * second concurrent start fail deterministically without touching the live
 * tree; locks from dead processes are stolen.
 *
 * Stdlib only — this file ships in the runtime image and replaces the old
 * copy-source-and-`next build` start.mjs (the multi-GB spawn compile).
 *
 * Layout expected in the image:
 *   /app/runtime/port          native no-basePath standalone artifact
 *   /app/runtime/path          sentinel-basePath standalone artifact
 *   /app/runtime/kz-next-relocations.json
 */

import { realpathSync } from "node:fs";
import { createHash } from "node:crypto";
import {
    cp,
    mkdir,
    readdir,
    readFile,
    rename,
    rm,
    stat,
    symlink,
    writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { spawn } from "node:child_process";
import { pathToFileURL } from "node:url";

import { countBufferOccurrences } from "./index-next-runtime.mjs";

const SENTINEL_FAMILY_RE = /__KZ_RUNTIME_BASE_[0-9A-F]+__/;
const SEGMENT_RE = /^[A-Za-z0-9._~-]+$/;
const MAX_PATH_LENGTH = 512;
const MAX_SEGMENT_LENGTH = 128;
const ALLOWED_KINDS = new Set(["js", "json", "html", "rsc", "css", "txt"]);
const SHA256_RE = /^[0-9a-f]{64}$/;

/**
 * Validate and normalize the runtime path: one leading slash, no trailing
 * slashes, conservative segment grammar, bounded length. Throws on anything
 * suspicious — env misconfiguration must fail before Next starts.
 */
export function validateRuntimePath(value) {
    if (typeof value !== "string" || value.trim() === "") {
        throw new Error("runtime path is empty");
    }
    // Raw control characters are rejected BEFORE trimming — a trailing
    // newline in an env value is a misconfiguration, not whitespace.
    if (/[\u0000-\u001f\u007f]/.test(value)) {
        throw new Error("runtime path contains control characters");
    }
    const trimmed = value.trim();
    if (trimmed.length > MAX_PATH_LENGTH) {
        throw new Error(`runtime path exceeds maximum length ${MAX_PATH_LENGTH}`);
    }
    if (SENTINEL_FAMILY_RE.test(trimmed)) {
        throw new Error("runtime path must not contain the relocation sentinel");
    }
    if (trimmed.includes("\\") || trimmed.includes("?") || trimmed.includes("#")) {
        throw new Error(`runtime path contains forbidden characters: ${JSON.stringify(trimmed)}`);
    }
    const withLeading = trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
    const withoutTrailing = withLeading.replace(/\/+$/, "");
    if (withoutTrailing === "") {
        throw new Error("runtime path normalizes to empty");
    }
    for (const segment of withoutTrailing.split("/").slice(1)) {
        if (
            segment === "" ||
            segment === "." ||
            segment === ".." ||
            segment.includes("%") ||
            !SEGMENT_RE.test(segment)
        ) {
            throw new Error(`invalid runtime path segment: ${JSON.stringify(segment)}`);
        }
        if (segment.length > MAX_SEGMENT_LENGTH) {
            throw new Error(`runtime path segment exceeds maximum length ${MAX_SEGMENT_LENGTH}`);
        }
    }
    return withoutTrailing;
}

/**
 * Resolve routing mode from the environment. Explicit mode wins; unset mode
 * falls back to path-if-nonempty for backward compatibility. Explicit path
 * mode without a usable path fails closed.
 */
export function resolveRoutingMode(env) {
    const mode = env.KAMIWAZA_ROUTING_MODE;
    const rawPath = (env.KAMIWAZA_APP_PATH ?? "").trim();

    if (mode === "port") {
        return { routingMode: "port", appPath: "" };
    }
    if (mode === "path") {
        if (rawPath === "") {
            throw new Error(
                "KAMIWAZA_ROUTING_MODE=path requires a nonempty KAMIWAZA_APP_PATH",
            );
        }
        return { routingMode: "path", appPath: validateRuntimePath(rawPath) };
    }
    if (mode != null && mode !== "") {
        throw new Error(`unknown KAMIWAZA_ROUTING_MODE: ${JSON.stringify(mode)}`);
    }
    if (rawPath !== "") {
        return { routingMode: "path", appPath: validateRuntimePath(rawPath) };
    }
    return { routingMode: "port", appPath: "" };
}

/** Byte-level replacement preserving all non-needle bytes exactly. */
function replaceBuffer(buffer, needle, replacement) {
    const chunks = [];
    let start = 0;
    let index = buffer.indexOf(needle);
    let occurrences = 0;
    while (index !== -1) {
        chunks.push(buffer.subarray(start, index), replacement);
        occurrences += 1;
        start = index + needle.length;
        index = buffer.indexOf(needle, start);
    }
    chunks.push(buffer.subarray(start));
    return { buffer: Buffer.concat(chunks), occurrences };
}

/**
 * Flight-aware .rsc relocation (B1). React Flight rows are either
 * newline-framed (`id:tag?json\n`) or byte-length-framed
 * (`id:T<hexlen>,<payload>` for long text). Replacing inside a T frame
 * changes the payload byte length, so the hex header is recomputed. A
 * sentinel inside any OTHER length-framed row type fails closed.
 */
export function transformRscBuffer(buffer, sentinel, replacement) {
    const sentinelBuffer = Buffer.from(sentinel, "utf8");
    const replacementBuffer = Buffer.from(replacement, "utf8");
    if (!buffer.includes(sentinelBuffer)) {
        return buffer;
    }

    const out = [];
    let pos = 0;
    const NEWLINE = 0x0a;

    while (pos < buffer.length) {
        // Row header: hex row id, then ':'
        let cursor = pos;
        while (
            cursor < buffer.length &&
            ((buffer[cursor] >= 0x30 && buffer[cursor] <= 0x39) ||
                (buffer[cursor] >= 0x61 && buffer[cursor] <= 0x66))
        ) {
            cursor += 1;
        }
        // Hint rows (`:HL[...]`) have an EMPTY id — a bare ':' is a valid
        // row start in real Next 15.5.19 output.
        if (buffer[cursor] !== 0x3a /* ':' */) {
            // Not a parseable row start. Fail only if a sentinel remains.
            const rest = buffer.subarray(pos);
            if (rest.includes(sentinelBuffer)) {
                throw new Error(
                    "unparseable Flight row containing the sentinel; refusing to relocate .rsc",
                );
            }
            out.push(rest);
            break;
        }
        cursor += 1; // past ':'

        // Length-framed row? tag letter + hex length + ','
        const tagByte = buffer[cursor];
        const isTagLetter =
            tagByte !== undefined &&
            ((tagByte >= 0x41 && tagByte <= 0x5a) || (tagByte >= 0x61 && tagByte <= 0x7a));
        let framed = null;
        if (isTagLetter) {
            let hexEnd = cursor + 1;
            while (
                hexEnd < buffer.length &&
                ((buffer[hexEnd] >= 0x30 && buffer[hexEnd] <= 0x39) ||
                    (buffer[hexEnd] >= 0x61 && buffer[hexEnd] <= 0x66))
            ) {
                hexEnd += 1;
            }
            if (hexEnd > cursor + 1 && buffer[hexEnd] === 0x2c /* ',' */) {
                const length = Number.parseInt(
                    buffer.subarray(cursor + 1, hexEnd).toString("latin1"),
                    16,
                );
                if (Number.isSafeInteger(length) && hexEnd + 1 + length <= buffer.length) {
                    framed = {
                        tag: String.fromCharCode(tagByte),
                        payloadStart: hexEnd + 1,
                        payloadEnd: hexEnd + 1 + length,
                    };
                }
            }
        }

        if (framed) {
            const payload = buffer.subarray(framed.payloadStart, framed.payloadEnd);
            if (payload.includes(sentinelBuffer)) {
                if (framed.tag !== "T") {
                    throw new Error(
                        `sentinel inside unsupported length-framed Flight row type ` +
                            `${JSON.stringify(framed.tag)}; refusing to relocate .rsc`,
                    );
                }
                const { buffer: newPayload } = replaceBuffer(
                    payload,
                    sentinelBuffer,
                    replacementBuffer,
                );
                out.push(
                    buffer.subarray(pos, cursor), // "id:" (cursor sits past ':')
                    Buffer.from(`T${newPayload.length.toString(16)},`, "latin1"),
                    newPayload,
                );
            } else {
                out.push(buffer.subarray(pos, framed.payloadEnd));
            }
            pos = framed.payloadEnd;
            continue;
        }

        // Newline-framed row (or final unterminated row).
        let lineEnd = buffer.indexOf(NEWLINE, cursor);
        lineEnd = lineEnd === -1 ? buffer.length : lineEnd + 1;
        const row = buffer.subarray(pos, lineEnd);
        if (row.includes(sentinelBuffer)) {
            out.push(replaceBuffer(row, sentinelBuffer, replacementBuffer).buffer);
        } else {
            out.push(row);
        }
        pos = lineEnd;
    }

    const result = Buffer.concat(out);
    if (result.includes(sentinelBuffer)) {
        throw new Error("residual sentinel after .rsc relocation; refusing to start");
    }
    return result;
}

/** Validate the relocation manifest before trusting any of it (S4). */
export function validateManifest(manifest) {
    if (manifest?.schemaVersion !== 1) {
        throw new Error(`unsupported relocation manifest schema: ${manifest?.schemaVersion}`);
    }
    if (
        typeof manifest.sentinel !== "string" ||
        !manifest.sentinel.startsWith("/") ||
        manifest.sentinel.length < 8
    ) {
        throw new Error("relocation manifest sentinel is missing or malformed");
    }
    if (typeof manifest.nextVersion !== "string" || manifest.nextVersion === "") {
        throw new Error("relocation manifest nextVersion is missing");
    }
    if (!Array.isArray(manifest.files) || manifest.files.length === 0) {
        throw new Error("relocation manifest has no files; the path artifact looks broken");
    }
    const seen = new Set();
    for (const entry of manifest.files) {
        if (typeof entry?.path !== "string" || entry.path === "") {
            throw new Error("relocation manifest entry has no path");
        }
        const normalized = path.posix.normalize(entry.path);
        if (
            normalized !== entry.path ||
            normalized.startsWith("/") ||
            normalized.startsWith("..") ||
            normalized.includes("/../")
        ) {
            throw new Error(`relocation manifest path escapes the artifact: ${entry.path}`);
        }
        if (
            normalized.startsWith("public/") ||
            normalized.startsWith("node_modules/") ||
            normalized.includes("/node_modules/")
        ) {
            throw new Error(
                `relocation manifest lists an unpatchable tree: ${entry.path} ` +
                    "(public/ and node_modules are never relocated)",
            );
        }
        if (seen.has(normalized)) {
            throw new Error(`duplicate relocation manifest path: ${entry.path}`);
        }
        seen.add(normalized);
        if (!ALLOWED_KINDS.has(entry.kind)) {
            throw new Error(`unknown relocation kind ${JSON.stringify(entry.kind)} for ${entry.path}`);
        }
        if (!Number.isSafeInteger(entry.size) || entry.size <= 0) {
            throw new Error(`invalid size for ${entry.path}`);
        }
        if (!Number.isSafeInteger(entry.occurrences) || entry.occurrences <= 0) {
            throw new Error(`invalid occurrence count for ${entry.path}`);
        }
        if (typeof entry.sha256 !== "string" || !SHA256_RE.test(entry.sha256)) {
            throw new Error(`invalid sha256 for ${entry.path}`);
        }
    }
    return manifest;
}

async function verifyManifestSources(sourceRoot, manifest) {
    const sentinelBuffer = Buffer.from(manifest.sentinel, "utf8");
    for (const entry of manifest.files) {
        const buffer = await readFile(path.join(sourceRoot, entry.path));
        const sha256 = createHash("sha256").update(buffer).digest("hex");
        if (sha256 !== entry.sha256 || buffer.length !== entry.size) {
            throw new Error(
                `relocation source hash mismatch for ${entry.path}; ` +
                    "the artifact does not match its relocation index (sha256)",
            );
        }
        const occurrences = countBufferOccurrences(buffer, sentinelBuffer);
        if (occurrences !== entry.occurrences) {
            throw new Error(
                `relocation occurrence count mismatch for ${entry.path}: ` +
                    `indexed ${entry.occurrences}, found ${occurrences}`,
            );
        }
    }
}

async function verifyArtifactNextVersion(sourceRoot, manifest) {
    let artifactVersion;
    try {
        const pkg = JSON.parse(
            await readFile(path.join(sourceRoot, "node_modules/next/package.json"), "utf8"),
        );
        artifactVersion = pkg.version;
    } catch {
        return; // fixture artifacts without a traced next package
    }
    if (artifactVersion !== manifest.nextVersion) {
        throw new Error(
            `artifact next@${artifactVersion} does not match relocation manifest ` +
                `next@${manifest.nextVersion}`,
        );
    }
}

async function mirrorTree(sourceRoot, targetRoot, relative, indexed, patch) {
    const absoluteSource = relative === "" ? sourceRoot : path.join(sourceRoot, relative);
    const entries = await readdir(absoluteSource, { withFileTypes: true });
    for (const entry of entries) {
        const rel = relative === "" ? entry.name : `${relative}/${entry.name}`;
        const sourcePath = path.join(sourceRoot, rel);
        const targetPath = path.join(targetRoot, rel);

        // Heavyweight prefix-free trees are linked wholesale (the indexer
        // proved them sentinel-free, including behind symlinks).
        if (relative === "" && (entry.name === "node_modules" || entry.name === "public")) {
            await symlink(sourcePath, targetPath);
            continue;
        }

        if (entry.isSymbolicLink()) {
            // Indexer policy guarantees in-root file symlinks only; make the
            // mirrored copy self-contained.
            await cp(sourcePath, targetPath, { dereference: true });
            continue;
        }
        if (entry.isDirectory()) {
            await mkdir(targetPath, { recursive: true });
            await mirrorTree(sourceRoot, targetRoot, rel, indexed, patch);
        } else if (entry.isFile()) {
            if (indexed.has(rel)) {
                await patch(rel, sourcePath, targetPath);
            } else if (rel === "server.js") {
                // server.js anchors __dirname; it must be a real file even
                // when (unusually) unindexed.
                await cp(sourcePath, targetPath);
            } else {
                await symlink(sourcePath, targetPath);
            }
        }
    }
}

async function scanForResidualSentinel(root, sentinel, relative = "") {
    const sentinelBuffer = Buffer.from(sentinel, "utf8");
    const absolute = relative === "" ? root : path.join(root, relative);
    const entries = await readdir(absolute, { withFileTypes: true });
    for (const entry of entries) {
        const rel = relative === "" ? entry.name : `${relative}/${entry.name}`;
        const entryPath = path.join(root, rel);
        if (entry.isSymbolicLink()) {
            const target = await stat(entryPath).catch((error) => {
                throw new Error(`unreadable symlink in runtime: ${rel} (${error.code})`);
            });
            if (target.isDirectory()) {
                // Only the two indexer-verified wholesale links are allowed.
                if (relative === "" && (entry.name === "node_modules" || entry.name === "public")) {
                    continue;
                }
                throw new Error(`unexpected directory symlink in runtime: ${rel}`);
            }
            const buffer = await readFile(entryPath);
            if (buffer.includes(sentinelBuffer)) {
                throw new Error(`residual sentinel found in ${rel}; refusing to start`);
            }
            continue;
        }
        if (entry.isDirectory()) {
            await scanForResidualSentinel(root, sentinel, rel);
        } else if (entry.isFile()) {
            const buffer = await readFile(entryPath);
            if (buffer.includes(sentinelBuffer)) {
                throw new Error(`residual sentinel found in ${rel}; relocation is incomplete`);
            }
        }
    }
}

function assertDisjointRoots(sourceRoot, targetRoot) {
    if (!path.isAbsolute(sourceRoot) || !path.isAbsolute(targetRoot)) {
        throw new Error("relocation roots must be absolute paths");
    }
    const source = path.resolve(sourceRoot);
    const target = path.resolve(targetRoot);
    if (
        source === target ||
        target.startsWith(`${source}${path.sep}`) ||
        source.startsWith(`${target}${path.sep}`)
    ) {
        throw new Error(
            `relocation roots must be disjoint (source ${source}, target ${target})`,
        );
    }
    return { source, target };
}

async function isProcessAlive(pid) {
    try {
        process.kill(pid, 0);
        return true;
    } catch (error) {
        return error.code === "EPERM";
    }
}

async function acquireStartupLock(target) {
    const lockDir = `${target}.lock`;
    const pidFile = path.join(lockDir, "pid");
    for (let attempt = 0; attempt < 2; attempt += 1) {
        try {
            await mkdir(lockDir);
            await writeFile(pidFile, String(process.pid));
            return lockDir;
        } catch (error) {
            if (error.code !== "EEXIST") {
                throw error;
            }
            const ownerPid = Number.parseInt(
                await readFile(pidFile, "utf8").catch(() => ""),
                10,
            );
            if (Number.isInteger(ownerPid) && (await isProcessAlive(ownerPid))) {
                throw new Error(
                    `another start (pid ${ownerPid}) holds the runtime lock ${lockDir}; ` +
                        "refusing to touch the live tree",
                );
            }
            // Stale lock from a dead process — steal it.
            await rm(lockDir, { recursive: true, force: true });
        }
    }
    throw new Error(`could not acquire runtime lock ${lockDir}`);
}

/**
 * Build the relocated runtime at targetRoot from the sentinel artifact at
 * sourceRoot. Fail-closed at every stage; publishes atomically via a
 * staging directory + rename. Returns preparation stats.
 */
export async function prepareRuntime({ sourceRoot, targetRoot, manifest, replacement }) {
    const startedAt = Date.now();

    validateManifest(manifest);
    const { source, target } = assertDisjointRoots(sourceRoot, targetRoot);
    const normalizedReplacement = validateRuntimePath(replacement);
    if (normalizedReplacement.includes(manifest.sentinel)) {
        throw new Error("replacement path must not contain the relocation sentinel");
    }

    // Verify everything BEFORE acquiring the lock or writing, so a corrupt
    // artifact leaves no half-built runtime behind.
    await verifyManifestSources(source, manifest);
    await verifyArtifactNextVersion(source, manifest);

    const lockDir = await acquireStartupLock(target);
    const staging = `${target}.staging-${process.pid}`;

    try {
        await rm(staging, { recursive: true, force: true });
        await mkdir(staging, { recursive: true });

        const sentinelBuffer = Buffer.from(manifest.sentinel, "utf8");
        const replacementBuffer = Buffer.from(normalizedReplacement, "utf8");
        const indexed = new Set(manifest.files.map((entry) => entry.path));
        const kinds = new Map(manifest.files.map((entry) => [entry.path, entry.kind]));
        const expectedOccurrences = manifest.files.reduce(
            (sum, entry) => sum + entry.occurrences,
            0,
        );
        let copiedBytes = 0;
        let patchedFiles = 0;
        let occurrences = 0;

        await mirrorTree(source, staging, "", indexed, async (rel, sourcePath, targetPath) => {
            const buffer = await readFile(sourcePath);
            let patched;
            if (kinds.get(rel) === "rsc") {
                occurrences += countBufferOccurrences(buffer, sentinelBuffer);
                patched = transformRscBuffer(buffer, manifest.sentinel, normalizedReplacement);
            } else {
                const result = replaceBuffer(buffer, sentinelBuffer, replacementBuffer);
                occurrences += result.occurrences;
                patched = result.buffer;
            }
            if (kinds.get(rel) === "json") {
                try {
                    JSON.parse(patched.toString("utf8"));
                } catch (error) {
                    throw new Error(`patched JSON does not parse: ${rel}: ${error.message}`);
                }
            }
            await writeFile(targetPath, patched);
            copiedBytes += patched.length;
            patchedFiles += 1;
        });

        // Every indexed file must have been patched with every indexed
        // occurrence — a manifest/mirror disagreement is fail-closed (B2).
        if (patchedFiles !== manifest.files.length || occurrences !== expectedOccurrences) {
            throw new Error(
                `relocation totals mismatch: patched ${patchedFiles}/${manifest.files.length} ` +
                    `files, ${occurrences}/${expectedOccurrences} occurrences; ` +
                    "the manifest lists files the mirror cannot patch",
            );
        }

        // Writable ephemeral cache for the running server.
        await mkdir(path.join(staging, ".next", "cache"), { recursive: true });

        await scanForResidualSentinel(staging, manifest.sentinel);

        // Atomic publish: replace any previous tree only after the staging
        // tree is fully verified.
        await rm(target, { recursive: true, force: true });
        await rename(staging, target);

        return {
            prepareMs: Date.now() - startedAt,
            copiedBytes,
            patchedFiles,
            occurrences,
            rssMib: Math.round(process.memoryUsage().rss / (1024 * 1024)),
        };
    } catch (error) {
        await rm(staging, { recursive: true, force: true }).catch(() => {});
        await rm(lockDir, { recursive: true, force: true }).catch(() => {});
        throw error;
    }
}

/** Conventional 128+N exit code for a signal name; 1 when unknown (N3). */
export function computeSignalExitCode(signal) {
    const number = os.constants.signals?.[signal];
    return Number.isInteger(number) ? 128 + number : 1;
}

export function startStandalone(runtimeRoot, env = process.env) {
    const child = spawn(process.execPath, [path.join(runtimeRoot, "server.js")], {
        cwd: runtimeRoot,
        env: {
            ...env,
            HOSTNAME: env.HOSTNAME || "0.0.0.0",
            PORT: env.PORT || "3000",
        },
        stdio: "inherit",
    });
    for (const signal of ["SIGINT", "SIGTERM"]) {
        process.on(signal, () => {
            if (child.exitCode === null && child.signalCode === null) {
                child.kill(signal);
            } else {
                process.exitCode = computeSignalExitCode(signal);
            }
        });
    }
    child.once("error", (error) => {
        console.error(
            JSON.stringify({
                event: "kz_next_runtime",
                severity: "critical",
                error: `failed to spawn standalone server: ${error.message}`,
            }),
        );
        process.exitCode = 1;
    });
    child.once("exit", (code, signal) => {
        process.exitCode = signal ? computeSignalExitCode(signal) : code ?? 1;
    });
    return child;
}

async function main() {
    const imageRoot = process.env.KZ_RUNTIME_IMAGE_ROOT || "/app/runtime";
    const targetRoot = process.env.KZ_RUNTIME_TARGET || "/tmp/kz-next-runtime";
    if (!path.isAbsolute(imageRoot) || !path.isAbsolute(targetRoot)) {
        throw new Error("KZ_RUNTIME_IMAGE_ROOT and KZ_RUNTIME_TARGET must be absolute");
    }
    // The relocated tree lives under /tmp (the only writable path on a
    // read-only rootfs). Overriding elsewhere is a test/dev-only escape
    // hatch — an arbitrary target could otherwise clobber image state (S7).
    if (
        !targetRoot.startsWith("/tmp/") &&
        process.env.KZ_RUNTIME_ALLOW_CUSTOM_TARGET !== "1"
    ) {
        throw new Error(
            `KZ_RUNTIME_TARGET must live under /tmp (got ${targetRoot}); ` +
                "set KZ_RUNTIME_ALLOW_CUSTOM_TARGET=1 only for tests",
        );
    }

    const routing = resolveRoutingMode(process.env);

    if (routing.routingMode === "port") {
        console.log(
            JSON.stringify({ event: "kz_next_runtime", mode: "port", action: "start-native" }),
        );
        startStandalone(path.join(imageRoot, "port"));
        return;
    }

    const manifest = JSON.parse(
        await readFile(path.join(imageRoot, "kz-next-relocations.json"), "utf8"),
    );
    const stats = await prepareRuntime({
        sourceRoot: path.join(imageRoot, "path"),
        targetRoot,
        manifest,
        replacement: routing.appPath,
    });
    console.log(
        JSON.stringify({
            event: "kz_next_runtime",
            mode: "path",
            appPath: routing.appPath,
            prepare_ms: stats.prepareMs,
            prepare_rss_mib: stats.rssMib,
            copied_bytes: stats.copiedBytes,
            patched_files: stats.patchedFiles,
            occurrences: stats.occurrences,
        }),
    );
    startStandalone(targetRoot);
}

const invokedHref = (() => {
    try {
        return process.argv[1] ? pathToFileURL(realpathSync(process.argv[1])).href : "";
    } catch {
        return "";
    }
})();
if (invokedHref === import.meta.url) {
    main().catch((error) => {
        console.error(
            JSON.stringify({
                event: "kz_next_runtime",
                severity: "critical",
                error: error.message,
            }),
        );
        process.exit(1);
    });
}
