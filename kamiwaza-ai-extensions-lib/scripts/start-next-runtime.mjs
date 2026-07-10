#!/usr/bin/env node
/**
 * Boot entrypoint for the App Garden dual-artifact Next runtime.
 *
 * Port mode: start the native no-base artifact as-is.
 * Path mode: build a sparse writable mirror of the sentinel artifact in /tmp
 * — copy + byte-replace the indexed files, symlink everything else — verify
 * fail-closed (hashes, counts, JSON parse, zero residual sentinel), then
 * `node server.js`.
 *
 * Stdlib only — this file ships in the runtime image and replaces the old
 * copy-source-and-`next build` start.mjs (the multi-GB spawn compile).
 *
 * Layout expected in the image:
 *   /app/runtime/port          native no-basePath standalone artifact
 *   /app/runtime/path          sentinel-basePath standalone artifact
 *   /app/runtime/kz-next-relocations.json
 */

import { createHash } from "node:crypto";
import {
    cp,
    mkdir,
    readdir,
    readFile,
    rm,
    symlink,
    writeFile,
} from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { spawn } from "node:child_process";
import { pathToFileURL } from "node:url";

const SENTINEL_SEGMENT_RE = /^__KZ_RUNTIME_BASE_[0-9A-F]+__$/;
const SEGMENT_RE = /^[A-Za-z0-9._~-]+$/;

/**
 * Validate and normalize the runtime path: one leading slash, no trailing
 * slashes, conservative segment grammar. Throws on anything suspicious —
 * env misconfiguration must fail before Next starts.
 */
export function validateRuntimePath(value) {
    if (typeof value !== "string" || value.trim() === "") {
        throw new Error("runtime path is empty");
    }
    const trimmed = value.trim();
    if (/[\u0000-\u001f\u007f]/.test(trimmed)) {
        throw new Error("runtime path contains control characters");
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
        if (SENTINEL_SEGMENT_RE.test(segment)) {
            throw new Error("runtime path must not contain the relocation sentinel");
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

function countOccurrences(haystack, needle) {
    let count = 0;
    let index = haystack.indexOf(needle);
    while (index !== -1) {
        count += 1;
        index = haystack.indexOf(needle, index + needle.length);
    }
    return count;
}

async function verifyManifestSources(sourceRoot, manifest) {
    for (const entry of manifest.files) {
        const buffer = await readFile(path.join(sourceRoot, entry.path));
        const sha256 = createHash("sha256").update(buffer).digest("hex");
        if (sha256 !== entry.sha256 || buffer.length !== entry.size) {
            throw new Error(
                `relocation source hash mismatch for ${entry.path}; ` +
                    "the artifact does not match its relocation index (sha256)",
            );
        }
        const occurrences = countOccurrences(buffer.toString("utf8"), manifest.sentinel);
        if (occurrences !== entry.occurrences) {
            throw new Error(
                `relocation occurrence count mismatch for ${entry.path}: ` +
                    `indexed ${entry.occurrences}, found ${occurrences}`,
            );
        }
    }
}

async function mirrorTree(sourceRoot, targetRoot, relative, indexed, patch) {
    const absoluteSource = relative === "" ? sourceRoot : path.join(sourceRoot, relative);
    const entries = await readdir(absoluteSource, { withFileTypes: true });
    for (const entry of entries) {
        const rel = relative === "" ? entry.name : `${relative}/${entry.name}`;
        const sourcePath = path.join(sourceRoot, rel);
        const targetPath = path.join(targetRoot, rel);

        // Heavyweight prefix-free trees are linked wholesale.
        if (relative === "" && (entry.name === "node_modules" || entry.name === "public")) {
            await symlink(sourcePath, targetPath);
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

async function scanForResidualSentinel(root, sentinel) {
    const sentinelBuffer = Buffer.from(sentinel, "utf8");
    const entries = await readdir(root, { withFileTypes: true });
    for (const entry of entries) {
        const absolute = path.join(root, entry.name);
        if (entry.isSymbolicLink()) {
            // Directory symlinks (node_modules, public) were proven
            // sentinel-free by the build-time indexer. File symlinks still
            // point at image bytes — read them so an indexer blind spot
            // cannot reach production silently.
            let buffer;
            try {
                buffer = await readFile(absolute);
            } catch {
                continue; // dir symlink or unreadable — skip
            }
            if (buffer.includes(sentinelBuffer)) {
                throw new Error(
                    `residual sentinel found in unindexed file ${entry.name}; refusing to start`,
                );
            }
            continue;
        }
        if (entry.isDirectory()) {
            await scanForResidualSentinel(absolute, sentinel);
        } else if (entry.isFile()) {
            const buffer = await readFile(absolute);
            if (buffer.includes(sentinelBuffer)) {
                throw new Error(
                    `residual sentinel found in ${absolute}; relocation is incomplete`,
                );
            }
        }
    }
}

/**
 * Build the relocated runtime at targetRoot from the sentinel artifact at
 * sourceRoot. Fail-closed: verifies the manifest against the artifact before
 * writing, parses every patched JSON file, and scans the result for residual
 * sentinels. Returns preparation stats.
 */
export async function prepareRuntime({ sourceRoot, targetRoot, manifest, replacement }) {
    const startedAt = Date.now();
    const normalizedReplacement = validateRuntimePath(replacement);

    // Verify everything BEFORE creating the target so a corrupt artifact
    // leaves no half-built runtime behind.
    await verifyManifestSources(sourceRoot, manifest);

    await rm(targetRoot, { recursive: true, force: true });
    await mkdir(targetRoot, { recursive: true });

    const indexed = new Set(manifest.files.map((entry) => entry.path));
    const kinds = new Map(manifest.files.map((entry) => [entry.path, entry.kind]));
    let copiedBytes = 0;
    let patchedFiles = 0;
    let occurrences = 0;

    await mirrorTree(sourceRoot, targetRoot, "", indexed, async (rel, sourcePath, targetPath) => {
        const text = await readFile(sourcePath, "utf8");
        occurrences += countOccurrences(text, manifest.sentinel);
        const patched = text.split(manifest.sentinel).join(normalizedReplacement);
        if (kinds.get(rel) === "json") {
            try {
                JSON.parse(patched);
            } catch (error) {
                throw new Error(`patched JSON does not parse: ${rel}: ${error.message}`);
            }
        }
        await writeFile(targetPath, patched);
        copiedBytes += Buffer.byteLength(patched, "utf8");
        patchedFiles += 1;
    });

    // Writable ephemeral cache for the running server.
    await mkdir(path.join(targetRoot, ".next", "cache"), { recursive: true });

    await scanForResidualSentinel(targetRoot, manifest.sentinel);

    return {
        prepareMs: Date.now() - startedAt,
        copiedBytes,
        patchedFiles,
        occurrences,
        rssMib: Math.round(process.memoryUsage().rss / (1024 * 1024)),
    };
}

const SIGNAL_EXIT_CODES = { SIGINT: 130, SIGTERM: 143 };

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
                process.exit(SIGNAL_EXIT_CODES[signal] ?? 1);
            }
        });
    }
    child.once("exit", (code, signal) => {
        process.exit(signal ? SIGNAL_EXIT_CODES[signal] ?? 1 : code ?? 1);
    });
    return child;
}

async function main() {
    const imageRoot = process.env.KZ_RUNTIME_IMAGE_ROOT || "/app/runtime";
    const targetRoot = process.env.KZ_RUNTIME_TARGET || "/tmp/kz-next-runtime";

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

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
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
