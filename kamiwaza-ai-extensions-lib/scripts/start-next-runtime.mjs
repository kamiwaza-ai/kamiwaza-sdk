#!/usr/bin/env node
/** Boot the App Garden dual-artifact Next runtime in port or path mode. */

import { spawn } from "node:child_process";
import { realpathSync } from "node:fs";
import { readFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

import {
    resolveRoutingMode,
    validateRuntimePath,
} from "./runtime-path-contract.mjs";
import { prepareRuntime, validateManifest } from "./runtime-preparation.mjs";
import { transformHtmlBuffer, transformRscBuffer } from "./flight-relocation.mjs";

export { resolveRoutingMode, validateRuntimePath };
export { prepareRuntime, validateManifest };
export { transformHtmlBuffer, transformRscBuffer };

/** Conventional 128+N exit code for a signal name; 1 when unknown. */
export function computeSignalExitCode(signal) {
    const number = os.constants.signals?.[signal];
    return Number.isInteger(number) ? 128 + number : 1;
}

export function standaloneNodeArgs(runtimeRoot, preserveSymlinks = false) {
    const args = [];
    if (preserveSymlinks) {
        args.push("--preserve-symlinks", "--preserve-symlinks-main");
    }
    args.push(path.join(runtimeRoot, "server.js"));
    return args;
}

export function startStandalone(runtimeRoot, env = process.env, preserveSymlinks = false) {
    const child = spawn(process.execPath, standaloneNodeArgs(runtimeRoot, preserveSymlinks), {
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

export function resolveRuntimeRoots(env) {
    const rawImageRoot = env.KZ_RUNTIME_IMAGE_ROOT || "/app/runtime";
    const rawTargetRoot = env.KZ_RUNTIME_TARGET || "/tmp/kz-next-runtime";
    if (!path.isAbsolute(rawImageRoot)) {
        throw new Error("KZ_RUNTIME_IMAGE_ROOT and KZ_RUNTIME_TARGET must be absolute");
    }
    if (!path.isAbsolute(rawTargetRoot)) {
        throw new Error("KZ_RUNTIME_IMAGE_ROOT and KZ_RUNTIME_TARGET must be absolute");
    }
    const imageRoot = path.resolve(rawImageRoot);
    const targetRoot = path.resolve(rawTargetRoot);
    const tempPrefix = `${path.resolve("/tmp")}${path.sep}`;
    if (env.KZ_RUNTIME_ALLOW_CUSTOM_TARGET === "1") {
        return { imageRoot, targetRoot };
    }
    if (!targetRoot.startsWith(tempPrefix)) {
        throw new Error(
            `KZ_RUNTIME_TARGET must live under /tmp (got ${targetRoot}); ` +
                "set KZ_RUNTIME_ALLOW_CUSTOM_TARGET=1 only for tests",
        );
    }
    return { imageRoot, targetRoot };
}

function startPortRuntime(imageRoot) {
    console.log(
        JSON.stringify({ event: "kz_next_runtime", mode: "port", action: "start-native" }),
    );
    startStandalone(path.join(imageRoot, "port"));
}

async function startPathRuntime(imageRoot, targetRoot, routing) {
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
    if (!routing.validateOnly) {
        // The relocated tree intentionally symlinks sentinel-free files back
        // to the image artifact. Preserve their target-tree identities so a
        // relative require cannot realpath back into unpatched image modules.
        startStandalone(targetRoot, process.env, true);
    }
}

export function parseCommandArgs(argv) {
    if (argv.length === 0) {
        return { validateOnly: false };
    }
    if (argv.length === 1 && argv[0] === "--validate-only") {
        return { validateOnly: true };
    }
    throw new Error(`unsupported runtime arguments: ${argv.join(" ")}`);
}

async function main() {
    const command = parseCommandArgs(process.argv.slice(2));
    const { imageRoot, targetRoot } = resolveRuntimeRoots(process.env);
    const routing = resolveRoutingMode(process.env);
    if (routing.routingMode === "port") {
        if (command.validateOnly) {
            throw new Error("--validate-only requires path routing mode");
        }
        startPortRuntime(imageRoot);
        return;
    }
    await startPathRuntime(imageRoot, targetRoot, { ...routing, ...command });
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
