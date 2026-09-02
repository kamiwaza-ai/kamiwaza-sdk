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
    assertNoRelocationSentinel,
    type KamiwazaClientRouting,
    type KamiwazaRuntimeConfig,
    normalizeAppPath,
    withAppPath,
} from "./shared";

declare global {
    // eslint-disable-next-line no-var
    var __KAMIWAZA_RUNTIME__: Readonly<KamiwazaClientRouting> | undefined;
}

export function getAppPath(): string {
    const bootstrap = globalThis.__KAMIWAZA_RUNTIME__;
    if (bootstrap != null) {
        // A present bootstrap must be internally consistent — fail closed on
        // a malformed one rather than silently guessing (N2).
        if (typeof bootstrap.appPath !== "string") {
            throw new Error("invalid runtime bootstrap: appPath must be a string");
        }
        if (bootstrap.routingMode !== "path" && bootstrap.routingMode !== "port") {
            throw new Error(
                `invalid runtime bootstrap mode: ${JSON.stringify(bootstrap.routingMode)}`,
            );
        }
        const appPath = normalizeAppPath(bootstrap.appPath);
        assertNoRelocationSentinel(appPath);
        if (
            (bootstrap.routingMode === "port" && appPath !== "") ||
            (bootstrap.routingMode === "path" && appPath === "")
        ) {
            throw new Error(
                `inconsistent runtime bootstrap: mode ${String(bootstrap.routingMode)} ` +
                    `with appPath ${JSON.stringify(bootstrap.appPath)}`,
            );
        }
        return appPath;
    }
    // Inlined at build time by withKamiwazaAppGarden(); sentinel in the path
    // artifact (relocated and residual-scanned before boot), empty in the port
    // artifact and next dev. It must remain readable during `next build`, when
    // server components intentionally render relocatable sentinel URLs.
    return normalizeAppPath(process.env.KZ_INTERNAL_BAKED_APP_PATH);
}

/** Whether a runtime bootstrap or baked build contract is present.
 *
 * Port-mode contracts intentionally resolve to an empty app path. Consumers
 * that retain a legacy fallback must distinguish that valid empty value from
 * an app that has not adopted the runtime contract yet.
 */
export function hasAppPathContract(): boolean {
    return (
        globalThis.__KAMIWAZA_RUNTIME__ != null ||
        typeof process.env.KZ_INTERNAL_BAKED_APP_PATH === "string"
    );
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
        // Prefix only same-origin URLs — an absolute URL to another origin
        // (or any URL on the server, where there is no window) is external
        // by definition (S1).
        const windowOrigin =
            typeof window !== "undefined" ? window.location.origin : undefined;
        if (windowOrigin === undefined || input.origin !== windowOrigin) {
            return fetch(input, init);
        }
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
            const response = await appFetch("/kamiwaza/runtime.json", {
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
