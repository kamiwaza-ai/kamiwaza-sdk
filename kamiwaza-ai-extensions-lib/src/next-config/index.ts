/**
 * Build-only Next config wrapper for App Garden apps.
 *
 * Owns basePath/assetPrefix for the dual-artifact contract:
 *   KZ_NEXT_BUILD_VARIANT=path  → sentinel basePath (relocated at boot)
 *   KZ_NEXT_BUILD_VARIANT=port  → native no-base build
 *   unset (plain `next dev`)    → native no-base development
 *
 * Also defines the reserved KZ_INTERNAL_BAKED_APP_PATH compile constant that
 * `getAppPath()` and `KamiwazaRuntimeBootstrap` read, and rejects options
 * that are incompatible with byte relocation (SRI, production source maps,
 * manual client base path, author basePath/assetPrefix).
 */

import { createRequire } from "node:module";

import type { NextConfig } from "next";

export const KAMIWAZA_BASE_PATH_SENTINEL = "/__KZ_RUNTIME_BASE_7F3A91C2__";

/** Exact Next versions the relocation contract is validated against. */
export const SUPPORTED_NEXT_VERSIONS: readonly string[] = ["15.5.19"];

export type KamiwazaBuildVariant = "path" | "port";

function detectNextVersion(): string | undefined {
    try {
        // Works from both the CJS and ESM bundle outputs.
        const requireFromHere = createRequire(
            typeof __filename !== "undefined" ? __filename : import.meta.url,
        );
        const pkg = requireFromHere("next/package.json") as { version?: string };
        return pkg.version;
    } catch {
        return undefined;
    }
}

/**
 * Internal indirection for tests. There is deliberately NO public way to
 * override the detected Next version — the exact pin is the compatibility
 * contract (B5).
 */
export const _internals = { detectNextVersion };

function resolveVariant(): KamiwazaBuildVariant | undefined {
    const variant = process.env.KZ_NEXT_BUILD_VARIANT;
    if (variant === "path" || variant === "port") {
        // `next dev` sets NODE_ENV=development before loading the config; a
        // stray variant (e.g. leaked into .env) must not turn dev into a
        // sentinel build with no relocator in front of it (N1).
        if (process.env.NODE_ENV === "development") {
            console.warn(
                `[withKamiwazaAppGarden] ignoring KZ_NEXT_BUILD_VARIANT=${variant} in development`,
            );
            return undefined;
        }
        return variant;
    }
    if (variant != null && variant !== "") {
        throw new Error(
            `KZ_NEXT_BUILD_VARIANT must be "path" or "port", got ${JSON.stringify(variant)}`,
        );
    }
    return undefined;
}

export function withKamiwazaAppGarden(config: NextConfig = {}): NextConfig {
    const variant = resolveVariant();

    if (config.basePath !== undefined) {
        throw new Error(
            "withKamiwazaAppGarden owns basePath; remove it from the app's next config",
        );
    }
    if (config.assetPrefix !== undefined) {
        throw new Error(
            "withKamiwazaAppGarden owns assetPrefix; remove it from the app's next config",
        );
    }
    if (config.output !== undefined && config.output !== "standalone") {
        throw new Error(
            'withKamiwazaAppGarden requires output: "standalone" for its production runtime',
        );
    }
    if (config.env?.KZ_INTERNAL_BAKED_APP_PATH !== undefined) {
        throw new Error("KZ_INTERNAL_BAKED_APP_PATH is reserved and cannot be set by apps");
    }
    if (config.productionBrowserSourceMaps) {
        throw new Error(
            "production browser source maps are incompatible with runtime relocation; remove productionBrowserSourceMaps",
        );
    }
    const experimental = (config.experimental ?? {}) as Record<string, unknown>;
    if (experimental.sri) {
        throw new Error(
            "experimental.sri is incompatible with runtime relocation (integrity would reject patched bytes)",
        );
    }
    if (experimental.serverSourceMaps) {
        throw new Error(
            "experimental.serverSourceMaps ships production source maps, which are incompatible with runtime relocation",
        );
    }
    if (experimental.manualClientBasePath) {
        throw new Error(
            "experimental.manualClientBasePath conflicts with the managed base path contract",
        );
    }
    if (experimental.isrFlushToDisk === true) {
        throw new Error(
            "experimental.isrFlushToDisk is incompatible with the ephemeral runtime overlay",
        );
    }

    if (
        variant !== undefined &&
        config.serverExternalPackages?.includes("@kamiwaza-ai/extensions-lib")
    ) {
        throw new Error(
            "@kamiwaza-ai/extensions-lib cannot be listed in serverExternalPackages " +
                "for production variants because runtime path constants must be bundled",
        );
    }

    // The pin only gates production image builds; plain `next dev` may run
    // whatever patch version is installed locally.
    if (variant !== undefined) {
        const nextVersion = _internals.detectNextVersion();
        if (nextVersion === undefined) {
            throw new Error(
                "could not detect the installed Next version; production build variants " +
                    "require the exact supported version to be resolvable",
            );
        }
        if (!SUPPORTED_NEXT_VERSIONS.includes(nextVersion)) {
            throw new Error(
                `Next ${nextVersion} is not validated for runtime relocation; ` +
                    `supported: ${SUPPORTED_NEXT_VERSIONS.join(", ")}. ` +
                    "Upgrades must pass the next-runtime canary first.",
            );
        }
    }

    const bakedAppPath = variant === "path" ? KAMIWAZA_BASE_PATH_SENTINEL : "";

    return {
        ...config,
        output: config.output ?? "standalone",
        ...(variant === "path"
            ? {
                  basePath: KAMIWAZA_BASE_PATH_SENTINEL,
                  assetPrefix: KAMIWAZA_BASE_PATH_SENTINEL,
              }
            : {}),
        env: {
            ...config.env,
            KZ_INTERNAL_BAKED_APP_PATH: bakedAppPath,
        },
        experimental: {
            ...config.experimental,
            // The App Garden runtime overlay is ephemeral; never flush ISR
            // output to the read-only image tree.
            isrFlushToDisk: false,
        },
    };
}
