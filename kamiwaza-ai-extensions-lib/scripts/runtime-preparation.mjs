/** Validate, relocate, verify, and atomically publish a Next standalone runtime. */

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
import path from "node:path";
import process from "node:process";

import { countBufferOccurrences } from "./index-next-runtime.mjs";
import {
    replaceBuffer,
    transformHtmlBuffer,
    transformRscBuffer,
} from "./flight-relocation.mjs";
import { validateRuntimePath } from "./runtime-path-contract.mjs";

const ALLOWED_KINDS = new Set(["js", "json", "html", "rsc", "css", "txt"]);
const SHA256_RE = /^[0-9a-f]{64}$/;
const SENTINEL_RE = /^\/__KZ_RUNTIME_BASE_[0-9A-F]+__$/;
const WHOLESALE_LINKS = new Set(["node_modules", "public"]);

function assertManifestSchema(manifest) {
    if (manifest?.schemaVersion !== 1) {
        throw new Error(`unsupported relocation manifest schema: ${manifest?.schemaVersion}`);
    }
}

function assertManifestSentinel(sentinel) {
    if (typeof sentinel !== "string") {
        throw new Error("relocation manifest sentinel is missing or malformed");
    }
    if (!sentinel.startsWith("/")) {
        throw new Error("relocation manifest sentinel is missing or malformed");
    }
    if (sentinel.length < 8) {
        throw new Error("relocation manifest sentinel is missing or malformed");
    }
    if (!SENTINEL_RE.test(sentinel)) {
        throw new Error("relocation manifest sentinel is missing or malformed");
    }
}

function assertManifestNextVersion(nextVersion) {
    if (typeof nextVersion !== "string") {
        throw new Error("relocation manifest nextVersion is missing");
    }
    if (nextVersion === "") {
        throw new Error("relocation manifest nextVersion is missing");
    }
}

function assertManifestFiles(files) {
    if (!Array.isArray(files)) {
        throw new Error("relocation manifest has no files; the path artifact looks broken");
    }
    if (files.length === 0) {
        throw new Error("relocation manifest has no files; the path artifact looks broken");
    }
}

function validateManifestHeader(manifest) {
    assertManifestSchema(manifest);
    assertManifestSentinel(manifest.sentinel);
    assertManifestNextVersion(manifest.nextVersion);
    assertManifestFiles(manifest.files);
}

function requireManifestEntryPath(entry) {
    if (typeof entry?.path !== "string") {
        throw new Error("relocation manifest entry has no path");
    }
    if (entry.path === "") {
        throw new Error("relocation manifest entry has no path");
    }
    return entry.path;
}

function assertContainedManifestPath(original, normalized) {
    if (normalized !== original) {
        throw new Error(`relocation manifest path escapes the artifact: ${original}`);
    }
    if (normalized.startsWith("/")) {
        throw new Error(`relocation manifest path escapes the artifact: ${original}`);
    }
    if (normalized.startsWith("..")) {
        throw new Error(`relocation manifest path escapes the artifact: ${original}`);
    }
}

function assertPatchableManifestPath(original, normalized) {
    if (normalized.startsWith("public/")) {
        throw new Error(`relocation manifest lists an unpatchable tree: ${original}`);
    }
    if (normalized.startsWith("node_modules/")) {
        throw new Error(`relocation manifest lists an unpatchable tree: ${original}`);
    }
    if (normalized.includes("/node_modules/")) {
        throw new Error(`relocation manifest lists an unpatchable tree: ${original}`);
    }
}

function validateManifestPath(entry) {
    const original = requireManifestEntryPath(entry);
    const normalized = path.posix.normalize(original);
    assertContainedManifestPath(original, normalized);
    assertPatchableManifestPath(original, normalized);
    return normalized;
}

function validatePositiveInteger(value, label, entryPath) {
    if (!Number.isSafeInteger(value)) {
        throw new Error(`invalid ${label} for ${entryPath}`);
    }
    if (value <= 0) {
        throw new Error(`invalid ${label} for ${entryPath}`);
    }
}

function validateSha256(value, entryPath) {
    if (typeof value !== "string") {
        throw new Error(`invalid sha256 for ${entryPath}`);
    }
    if (!SHA256_RE.test(value)) {
        throw new Error(`invalid sha256 for ${entryPath}`);
    }
}

function validateManifestEntry(entry, seen) {
    const normalized = validateManifestPath(entry);
    if (seen.has(normalized)) {
        throw new Error(`duplicate relocation manifest path: ${entry.path}`);
    }
    seen.add(normalized);
    if (!ALLOWED_KINDS.has(entry.kind)) {
        throw new Error(`unknown relocation kind ${JSON.stringify(entry.kind)} for ${entry.path}`);
    }
    validatePositiveInteger(entry.size, "size", entry.path);
    validatePositiveInteger(entry.occurrences, "occurrence count", entry.path);
    validateSha256(entry.sha256, entry.path);
}

/** Validate the relocation manifest before trusting any of it. */
export function validateManifest(manifest) {
    validateManifestHeader(manifest);
    const seen = new Set();
    for (const entry of manifest.files) {
        validateManifestEntry(entry, seen);
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
    const packagePath = path.join(sourceRoot, "node_modules/next/package.json");
    const pkg = JSON.parse(await readFile(packagePath, "utf8"));
    if (pkg.version !== manifest.nextVersion) {
        throw new Error(
            `artifact next@${pkg.version} does not match relocation manifest ` +
                `next@${manifest.nextVersion}`,
        );
    }
}

function isWholesaleRootEntry(relative, entryName) {
    return relative === "" && WHOLESALE_LINKS.has(entryName);
}

async function mirrorEntry(context, relative, entry) {
    const rel = relative === "" ? entry.name : `${relative}/${entry.name}`;
    const sourcePath = path.join(context.sourceRoot, rel);
    const targetPath = path.join(context.targetRoot, rel);
    if (isWholesaleRootEntry(relative, entry.name)) {
        await symlink(sourcePath, targetPath);
        return;
    }
    if (entry.isSymbolicLink()) {
        await cp(sourcePath, targetPath, { dereference: true });
        return;
    }
    if (entry.isDirectory()) {
        await mkdir(targetPath, { recursive: true });
        await mirrorTree(context, rel);
        return;
    }
    if (!entry.isFile()) {
        return;
    }
    if (context.indexed.has(rel)) {
        await context.patch(rel, sourcePath, targetPath);
        return;
    }
    if (rel === "server.js") {
        await cp(sourcePath, targetPath);
        return;
    }
    await symlink(sourcePath, targetPath);
}

async function mirrorTree(context, relative = "") {
    const absoluteSource =
        relative === "" ? context.sourceRoot : path.join(context.sourceRoot, relative);
    const entries = await readdir(absoluteSource, { withFileTypes: true });
    for (const entry of entries) {
        await mirrorEntry(context, relative, entry);
    }
}

async function assertFileHasNoSentinel(entryPath, rel, sentinelBuffer) {
    const buffer = await readFile(entryPath);
    if (buffer.includes(sentinelBuffer)) {
        throw new Error(`residual sentinel found in ${rel}; relocation is incomplete`);
    }
}

async function scanRuntimeSymlink(context, item) {
    const target = await stat(item.entryPath).catch((error) => {
        throw new Error(`unreadable symlink in runtime: ${item.rel} (${error.code})`);
    });
    if (target.isDirectory()) {
        if (isWholesaleRootEntry(item.relative, item.entry.name)) {
            return;
        }
        throw new Error(`unexpected directory symlink in runtime: ${item.rel}`);
    }
    await assertFileHasNoSentinel(item.entryPath, item.rel, context.sentinelBuffer);
}

async function scanRuntimeEntry(context, relative, entry) {
    const rel = relative === "" ? entry.name : `${relative}/${entry.name}`;
    const entryPath = path.join(context.root, rel);
    if (entry.isSymbolicLink()) {
        await scanRuntimeSymlink(context, { relative, entry, rel, entryPath });
        return;
    }
    if (entry.isDirectory()) {
        await scanRuntimeTree(context, rel);
        return;
    }
    if (entry.isFile()) {
        await assertFileHasNoSentinel(entryPath, rel, context.sentinelBuffer);
    }
}

async function scanRuntimeTree(context, relative = "") {
    const absolute = relative === "" ? context.root : path.join(context.root, relative);
    const entries = await readdir(absolute, { withFileTypes: true });
    for (const entry of entries) {
        await scanRuntimeEntry(context, relative, entry);
    }
}

async function scanForResidualSentinel(root, sentinel) {
    await scanRuntimeTree({ root, sentinelBuffer: Buffer.from(sentinel, "utf8") });
}

function assertAbsoluteRoot(root) {
    if (!path.isAbsolute(root)) {
        throw new Error("relocation roots must be absolute paths");
    }
}

function rootsOverlap(source, target) {
    if (source === target) {
        return true;
    }
    if (target.startsWith(`${source}${path.sep}`)) {
        return true;
    }
    return source.startsWith(`${target}${path.sep}`);
}

function assertDisjointRoots(sourceRoot, targetRoot) {
    assertAbsoluteRoot(sourceRoot);
    assertAbsoluteRoot(targetRoot);
    const source = path.resolve(sourceRoot);
    const target = path.resolve(targetRoot);
    if (rootsOverlap(source, target)) {
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

async function createStartupLock(lockDir, pidFile) {
    try {
        await mkdir(lockDir);
    } catch (error) {
        if (error.code === "EEXIST") {
            return false;
        }
        throw error;
    }
    try {
        await writeFile(pidFile, String(process.pid));
        return true;
    } catch (error) {
        await rm(lockDir, { recursive: true, force: true }).catch(() => {});
        throw error;
    }
}

async function readLockOwner(pidFile) {
    const raw = await readFile(pidFile, "utf8").catch(() => "");
    const ownerPid = Number.parseInt(raw, 10);
    return Number.isInteger(ownerPid) && ownerPid > 0 ? ownerPid : null;
}

async function assertLockIsStale(lockDir, pidFile) {
    const ownerPid = await readLockOwner(pidFile);
    if (ownerPid === null) {
        throw new Error(
            `runtime lock ${lockDir} has no valid owner metadata; ` +
                "refusing to touch the live tree",
        );
    }
    if (await isProcessAlive(ownerPid)) {
        throw new Error(
            `another start (pid ${ownerPid}) holds the runtime lock ${lockDir}; ` +
                "refusing to touch the live tree",
        );
    }
}

async function acquireStartupLock(target) {
    const lockDir = `${target}.lock`;
    const pidFile = path.join(lockDir, "pid");
    for (let attempt = 0; attempt < 2; attempt += 1) {
        if (await createStartupLock(lockDir, pidFile)) {
            return lockDir;
        }
        await assertLockIsStale(lockDir, pidFile);
        await rm(lockDir, { recursive: true, force: true });
    }
    throw new Error(`could not acquire runtime lock ${lockDir}`);
}

function validatePatchedJson(buffer, rel) {
    try {
        JSON.parse(buffer.toString("utf8"));
    } catch (error) {
        throw new Error(`patched JSON does not parse: ${rel}: ${error.message}`);
    }
}

function transformIndexedBuffer(context, rel, buffer) {
    const kind = context.kinds.get(rel);
    context.occurrences += countBufferOccurrences(buffer, context.sentinelBuffer);
    if (kind === "rsc") {
        return transformRscBuffer(buffer, context.manifest.sentinel, context.replacement);
    }
    if (kind === "html") {
        return transformHtmlBuffer(buffer, context.manifest.sentinel, context.replacement);
    }
    return replaceBuffer(buffer, context.sentinelBuffer, context.replacementBuffer).buffer;
}

async function patchIndexedFile(context, rel, sourcePath, targetPath) {
    const source = await readFile(sourcePath);
    const patched = transformIndexedBuffer(context, rel, source);
    if (context.kinds.get(rel) === "json") {
        validatePatchedJson(patched, rel);
    }
    await writeFile(targetPath, patched);
    context.copiedBytes += patched.length;
    context.patchedFiles += 1;
}

function assertRelocationTotals(context) {
    const expectedFiles = context.manifest.files.length;
    if (context.patchedFiles !== expectedFiles) {
        throw new Error(
            `relocation totals mismatch: patched ${context.patchedFiles}/${expectedFiles} files`,
        );
    }
    if (context.occurrences !== context.expectedOccurrences) {
        throw new Error(
            `relocation totals mismatch: patched ${context.occurrences}/` +
                `${context.expectedOccurrences} occurrences`,
        );
    }
}

function createRelocationContext(source, staging, manifest, replacement) {
    return {
        sourceRoot: source,
        targetRoot: staging,
        manifest,
        replacement,
        sentinelBuffer: Buffer.from(manifest.sentinel, "utf8"),
        replacementBuffer: Buffer.from(replacement, "utf8"),
        indexed: new Set(manifest.files.map((entry) => entry.path)),
        kinds: new Map(manifest.files.map((entry) => [entry.path, entry.kind])),
        expectedOccurrences: manifest.files.reduce(
            (sum, entry) => sum + entry.occurrences,
            0,
        ),
        copiedBytes: 0,
        patchedFiles: 0,
        occurrences: 0,
    };
}

async function buildRelocatedRuntime({ source, target, staging, manifest, replacement }) {
    await rm(staging, { recursive: true, force: true });
    await mkdir(staging, { recursive: true });
    const context = createRelocationContext(source, staging, manifest, replacement);
    await mirrorTree({
        ...context,
        patch: (rel, sourcePath, targetPath) =>
            patchIndexedFile(context, rel, sourcePath, targetPath),
    });
    assertRelocationTotals(context);
    await mkdir(path.join(staging, ".next", "cache"), { recursive: true });
    await scanForResidualSentinel(staging, manifest.sentinel);
    await rm(target, { recursive: true, force: true });
    await rename(staging, target);
    return context;
}

/** Build and atomically publish a verified relocated runtime. */
export async function prepareRuntime({ sourceRoot, targetRoot, manifest, replacement }) {
    const startedAt = Date.now();
    validateManifest(manifest);
    const { source, target } = assertDisjointRoots(sourceRoot, targetRoot);
    const normalizedReplacement = validateRuntimePath(replacement);
    await verifyManifestSources(source, manifest);
    await verifyArtifactNextVersion(source, manifest);

    const lockDir = await acquireStartupLock(target);
    const staging = `${target}.staging-${process.pid}`;
    try {
        const context = await buildRelocatedRuntime({
            source,
            target,
            staging,
            manifest,
            replacement: normalizedReplacement,
        });
        return {
            prepareMs: Date.now() - startedAt,
            copiedBytes: context.copiedBytes,
            patchedFiles: context.patchedFiles,
            occurrences: context.occurrences,
            rssMib: Math.round(process.memoryUsage().rss / (1024 * 1024)),
        };
    } catch (error) {
        await rm(staging, { recursive: true, force: true }).catch(() => {});
        throw error;
    } finally {
        await rm(lockDir, { recursive: true, force: true }).catch(() => {});
    }
}
