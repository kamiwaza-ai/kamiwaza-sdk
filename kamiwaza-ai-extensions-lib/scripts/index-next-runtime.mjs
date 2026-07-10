#!/usr/bin/env node
/**
 * Relocation indexer for the App Garden dual-artifact Next runtime.
 *
 * Runs at image-build time against the ASSEMBLED path-variant runtime
 * (standalone server.js + .next + public + traced node_modules). Records
 * every sentinel-bearing text file with occurrence count, size, and sha256
 * so the boot relocator can verify and patch fail-closed.
 *
 * Fail-closed conditions (image build fails):
 *   - sentinel in a binary/unrecognized file, in node_modules, or under
 *     public/ (public assets are served verbatim and never patched)
 *   - any source map outside node_modules
 *   - any broken, cyclic, directory, or root-escaping symlink; or a
 *     sentinel behind a file symlink
 *   - a .next/cache directory in the artifact
 *   - mandatory relocation roles with zero occurrences
 *
 * Stdlib only — this file ships in the runtime image.
 *
 * CLI:
 *   node index-next-runtime.mjs --root /out/path \
 *     --sentinel /__KZ_RUNTIME_BASE_7F3A91C2__ \
 *     --next-version 15.5.19 --output /out/kz-next-relocations.json
 */

import { createHash } from "node:crypto";
import { readdir, readFile, realpath, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

export const MANIFEST_SCHEMA_VERSION = 1;

const TEXT_KINDS = new Map([
    [".js", "js"],
    [".mjs", "js"],
    [".cjs", "js"],
    [".json", "json"],
    [".meta", "json"], // Next static output: redirect/route metadata
    [".html", "html"],
    [".htm", "html"],
    [".rsc", "rsc"],
    [".css", "css"],
    [".txt", "txt"],
]);

// Content types whose .body payload is safe to treat as relocatable text.
const TEXTUAL_BODY_CONTENT_TYPE_RE =
    /^(text\/|application\/(json|javascript|xml|x-ndjson|xhtml\+xml)|image\/svg\+xml)/i;

// Roles that must contain the sentinel in a healthy path-variant build. If
// none of a role's candidate files carries an occurrence, the artifact is
// broken and the image build must fail.
const MANDATORY_ROLES = [
    { name: "standalone server", test: (p) => p === "server.js" },
    {
        name: "server config",
        test: (p) => p === ".next/required-server-files.json" || p === ".next/routes-manifest.json",
    },
    { name: "client chunks", test: (p) => p.startsWith(".next/static/") && p.endsWith(".js") },
];

/** Count occurrences of an ASCII needle at the byte level. */
export function countBufferOccurrences(buffer, needleBuffer) {
    if (needleBuffer.length === 0) {
        throw new Error("empty needle");
    }
    let count = 0;
    let index = buffer.indexOf(needleBuffer);
    while (index !== -1) {
        count += 1;
        index = buffer.indexOf(needleBuffer, index + needleBuffer.length);
    }
    return count;
}

async function checkSymlink(root, rel) {
    const absolute = path.join(root, rel);
    let resolved;
    try {
        resolved = await realpath(absolute);
    } catch (error) {
        throw new Error(
            `broken or cyclic symlink in artifact: ${rel} (${error.code ?? error.message})`,
        );
    }
    const rootReal = await realpath(root);
    if (resolved !== rootReal && !resolved.startsWith(`${rootReal}${path.sep}`)) {
        throw new Error(`symlink escapes the artifact root: ${rel} -> ${resolved}`);
    }
    const target = await stat(resolved);
    if (target.isDirectory()) {
        throw new Error(`directory symlinks are not supported in the artifact: ${rel}`);
    }
    return resolved;
}

async function walk(root, relative = "") {
    const absolute = relative === "" ? root : path.join(root, relative);
    const entries = await readdir(absolute, { withFileTypes: true });
    const files = [];
    for (const entry of entries) {
        const rel = relative === "" ? entry.name : `${relative}/${entry.name}`;
        if (entry.isSymbolicLink()) {
            files.push({ rel, symlink: true });
        } else if (entry.isDirectory()) {
            files.push(...(await walk(root, rel)));
        } else if (entry.isFile()) {
            files.push({ rel, symlink: false });
        }
    }
    return files;
}

async function classifyBody(root, rel, sentinel) {
    const metaPath = path.join(root, rel.replace(/\.body$/, ".meta"));
    let contentType = "";
    try {
        const meta = JSON.parse(await readFile(metaPath, "utf8"));
        contentType =
            meta?.headers?.["content-type"] ?? meta?.headers?.["Content-Type"] ?? "";
    } catch {
        contentType = "";
    }
    if (TEXTUAL_BODY_CONTENT_TYPE_RE.test(contentType)) {
        return "txt";
    }
    throw new Error(
        `sentinel found in non-textual .body ${rel} (content-type ${JSON.stringify(contentType)}); ` +
            "binary response bodies cannot be relocated",
    );
}

/**
 * Scan an assembled runtime tree and produce the relocation manifest.
 * Throws on any fail-closed condition.
 */
export async function buildRelocationManifest({ root, sentinel, nextVersion }) {
    if (!sentinel || !sentinel.startsWith("/")) {
        throw new Error(`sentinel must be an absolute path, got ${JSON.stringify(sentinel)}`);
    }

    try {
        await stat(path.join(root, ".next/cache"));
        throw new Error(
            ".next/cache must not ship in the runtime artifact (build caches are not relocatable)",
        );
    } catch (error) {
        if (error.code !== "ENOENT") {
            throw error;
        }
    }

    const sentinelBuffer = Buffer.from(sentinel, "utf8");
    const files = [];

    for (const { rel, symlink } of await walk(root)) {
        const inNodeModules = rel.startsWith("node_modules/") || rel.includes("/node_modules/");

        if (symlink) {
            const resolved = await checkSymlink(root, rel);
            const buffer = await readFile(resolved);
            if (buffer.includes(sentinelBuffer)) {
                throw new Error(
                    `sentinel found behind symlink ${rel}; symlinked content is never relocated`,
                );
            }
            continue;
        }

        const buffer = await readFile(path.join(root, rel));
        const hasSentinel = buffer.includes(sentinelBuffer);

        if (inNodeModules) {
            if (hasSentinel) {
                throw new Error(
                    `sentinel found in node_modules (${rel}); dependencies must not embed the base path`,
                );
            }
            continue;
        }

        if (rel.endsWith(".map")) {
            throw new Error(
                `source map in runtime artifact: ${rel}; production source maps must not ship ` +
                    "(relocation would desynchronize them)",
            );
        }

        if (!hasSentinel) {
            continue;
        }

        if (rel.startsWith("public/")) {
            throw new Error(
                `sentinel found under public/ (${rel}); public assets are served verbatim — ` +
                    "use appAsset()/runtime config instead of baking the base path into public files",
            );
        }

        let kind = TEXT_KINDS.get(path.extname(rel).toLowerCase());
        if (kind === undefined && rel.endsWith(".body")) {
            kind = await classifyBody(root, rel, sentinel);
        }
        if (kind === undefined) {
            throw new Error(
                `sentinel found in binary or unrecognized file ${rel}; refusing to index it for relocation`,
            );
        }

        files.push({
            path: rel,
            size: buffer.length,
            sha256: createHash("sha256").update(buffer).digest("hex"),
            occurrences: countBufferOccurrences(buffer, sentinelBuffer),
            kind,
        });
    }

    for (const role of MANDATORY_ROLES) {
        if (!files.some((file) => role.test(file.path) && file.occurrences > 0)) {
            throw new Error(
                `mandatory relocation role has no sentinel occurrences: ${role.name}; ` +
                    "the path-variant build looks broken",
            );
        }
    }

    files.sort((a, b) => (a.path < b.path ? -1 : 1));

    return {
        schemaVersion: MANIFEST_SCHEMA_VERSION,
        nextVersion,
        sentinel,
        files,
    };
}

function parseArgs(argv) {
    const args = {};
    for (let i = 0; i < argv.length; i += 2) {
        const key = argv[i];
        const value = argv[i + 1];
        if (!key?.startsWith("--") || value === undefined) {
            throw new Error(`invalid arguments near ${JSON.stringify(key)}`);
        }
        args[key.slice(2)] = value;
    }
    return args;
}

async function readArtifactNextVersion(root) {
    try {
        const pkg = JSON.parse(
            await readFile(path.join(root, "node_modules/next/package.json"), "utf8"),
        );
        return pkg.version;
    } catch {
        return undefined;
    }
}

async function main() {
    const args = parseArgs(process.argv.slice(2));
    const { root, sentinel, output } = args;
    const nextVersion = args["next-version"];
    if (!root || !sentinel || !nextVersion || !output) {
        console.error(
            "usage: index-next-runtime.mjs --root DIR --sentinel PATH --next-version X.Y.Z --output FILE",
        );
        return 2;
    }
    // The CLI-claimed version must match the artifact's traced Next — the
    // manifest version is part of the compatibility contract (B5).
    const artifactVersion = await readArtifactNextVersion(root);
    if (artifactVersion !== undefined && artifactVersion !== nextVersion) {
        console.error(
            `[kz-next-index] FATAL: --next-version ${nextVersion} does not match the ` +
                `artifact's traced next@${artifactVersion}`,
        );
        return 1;
    }
    const manifest = await buildRelocationManifest({ root, sentinel, nextVersion });
    await writeFile(output, `${JSON.stringify(manifest, null, 2)}\n`);
    console.log(
        `[kz-next-index] indexed ${manifest.files.length} files, ` +
            `${manifest.files.reduce((sum, f) => sum + f.occurrences, 0)} occurrences`,
    );
    return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
    main().then(
        (code) => process.exit(code),
        (error) => {
            console.error(`[kz-next-index] FATAL: ${error.message}`);
            process.exit(1);
        },
    );
}
