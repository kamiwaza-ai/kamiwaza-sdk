#!/usr/bin/env node
/**
 * Relocation indexer for the App Garden dual-artifact Next runtime.
 *
 * Runs at image-build time against the ASSEMBLED path-variant runtime
 * (standalone server.js + .next + public + traced node_modules). Records
 * every sentinel-bearing text file with occurrence count, size, and sha256
 * so the boot relocator can verify and patch fail-closed. Any sentinel in a
 * binary/unrecognized file, in node_modules, in a source map, or a present
 * .next/cache directory fails the image build.
 *
 * Stdlib only — this file ships in the runtime image.
 *
 * CLI:
 *   node index-next-runtime.mjs --root /out/path \
 *     --sentinel /__KZ_RUNTIME_BASE_7F3A91C2__ \
 *     --next-version 15.5.19 --output /out/kz-next-relocations.json
 */

import { createHash } from "node:crypto";
import { readdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

export const MANIFEST_SCHEMA_VERSION = 1;

const TEXT_KINDS = new Map([
    [".js", "js"],
    [".mjs", "js"],
    [".cjs", "js"],
    [".json", "json"],
    [".html", "html"],
    [".htm", "html"],
    [".rsc", "rsc"],
    [".css", "css"],
    [".txt", "txt"],
    [".body", "txt"],
]);

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

function countOccurrences(haystack, needle) {
    let count = 0;
    let index = haystack.indexOf(needle);
    while (index !== -1) {
        count += 1;
        index = haystack.indexOf(needle, index + needle.length);
    }
    return count;
}

async function walk(root, relative = "") {
    const absolute = path.join(root, relative);
    const entries = await readdir(absolute, { withFileTypes: true });
    const files = [];
    for (const entry of entries) {
        const rel = relative === "" ? entry.name : `${relative}/${entry.name}`;
        if (entry.isSymbolicLink()) {
            continue;
        }
        if (entry.isDirectory()) {
            files.push(...(await walk(root, rel)));
        } else if (entry.isFile()) {
            files.push(rel);
        }
    }
    return files;
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

    for (const rel of await walk(root)) {
        const buffer = await readFile(path.join(root, rel));
        if (!buffer.includes(sentinelBuffer)) {
            continue;
        }

        const text = buffer.toString("utf8");
        const occurrences = countOccurrences(text, sentinel);

        if (rel.startsWith("node_modules/") || rel.includes("/node_modules/")) {
            throw new Error(
                `sentinel found in node_modules (${rel}); dependencies must not embed the base path`,
            );
        }
        if (rel.endsWith(".map")) {
            throw new Error(
                `sentinel found in source map ${rel}; production source maps must not ship in the runtime`,
            );
        }
        const kind = TEXT_KINDS.get(path.extname(rel).toLowerCase());
        if (kind === undefined) {
            throw new Error(
                `sentinel found in binary or unrecognized file ${rel}; refusing to index it for relocation`,
            );
        }

        files.push({
            path: rel,
            size: buffer.length,
            sha256: createHash("sha256").update(buffer).digest("hex"),
            occurrences,
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
