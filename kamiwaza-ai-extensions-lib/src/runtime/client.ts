/**
 * Client-side runtime-path access.
 *
 * `getAppPath()` is synchronous and safe pre-hydration: it reads the inline
 * bootstrap global installed by `KamiwazaRuntimeBootstrap` in the browser,
 * and falls back to the wrapper-defined `KZ_INTERNAL_BAKED_APP_PATH` compile
 * constant on the server/prerender path (the sentinel value there is
 * corrected by the boot relocator). Full deployment details are lazy via
 * `loadKamiwazaRuntime()` — nothing in the default hydration path calls it.
 */

import {
    type KamiwazaClientRouting,
    type KamiwazaRuntimeConfig,
    withAppPath,
} from "./shared";

declare global {
    // eslint-disable-next-line no-var
    var __KAMIWAZA_RUNTIME__: Readonly<KamiwazaClientRouting> | undefined;
}

export function getAppPath(): string {
    const bootstrap = globalThis.__KAMIWAZA_RUNTIME__;
    if (bootstrap && typeof bootstrap.appPath === "string") {
        return bootstrap.appPath;
    }
    // Inlined at build time by withKamiwazaAppGarden(); sentinel in the path
    // artifact (relocated at boot), empty in the port artifact and next dev.
    return process.env.KZ_INTERNAL_BAKED_APP_PATH ?? "";
}

/** Prefix a `public/`-root asset path for the current deployment. */
export function appAsset(path: string): string {
    return withAppPath(path, getAppPath());
}

/**
 * `fetch` with same-app prefixing. String and same-origin URL inputs that
 * are root-relative get the app path; absolute external URLs and Request
 * objects pass through untouched (no global fetch monkey-patching).
 */
export function appFetch(
    input: string | URL | Request,
    init?: RequestInit,
): Promise<Response> {
    if (typeof input === "string") {
        return fetch(withAppPath(input, getAppPath()), init);
    }
    if (input instanceof URL) {
        const prefixed = new URL(input);
        prefixed.pathname = withAppPath(input.pathname, getAppPath());
        return fetch(prefixed, init);
    }
    return fetch(input, init);
}

let cachedRuntime: Readonly<KamiwazaRuntimeConfig> | null = null;
let pendingRuntime: Promise<Readonly<KamiwazaRuntimeConfig>> | null = null;

/**
 * Lazily fetch the full runtime config from the scaffold's no-store JSON
 * route. Memoized on success; a failed fetch is not cached so callers can
 * retry.
 */
export function loadKamiwazaRuntime(): Promise<Readonly<KamiwazaRuntimeConfig>> {
    if (cachedRuntime) {
        return Promise.resolve(cachedRuntime);
    }
    if (pendingRuntime) {
        return pendingRuntime;
    }
    pendingRuntime = (async () => {
        try {
            const response = await appFetch("/__kamiwaza/runtime.json", {
                cache: "no-store",
            });
            if (!response.ok) {
                throw new Error(`runtime config request failed: ${response.status}`);
            }
            cachedRuntime = Object.freeze(
                (await response.json()) as KamiwazaRuntimeConfig,
            );
            return cachedRuntime;
        } finally {
            pendingRuntime = null;
        }
    })();
    return pendingRuntime;
}

/** Test hook: clear the memoized runtime config. */
export function __resetKamiwazaRuntimeCacheForTests(): void {
    cachedRuntime = null;
    pendingRuntime = null;
}
