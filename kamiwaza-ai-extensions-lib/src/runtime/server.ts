/**
 * Server-side runtime config resolution.
 *
 * Environment is authoritative for deployment identity: `x-forwarded-prefix`
 * may be compared for diagnostics but never selects the path. Precedence in
 * path mode: KAMIWAZA_APP_PATH_URL, then KAMIWAZA_APP_URL, then
 * KAMIWAZA_ORIGIN + appPath. Port mode uses KAMIWAZA_APP_URL.
 */

import {
    type KamiwazaRuntimeConfig,
    resolveRuntimeRouting,
} from "./shared";

function trimTrailingSlash(value: string): string {
    return value.replace(/\/+$/, "");
}

function resolveAppUrls(
    env: NodeJS.ProcessEnv,
    appPath: string,
): { appPathUrl: string; appUrl: string } {
    if (appPath === "") {
        return {
            appPathUrl: "",
            appUrl: trimTrailingSlash(env.KAMIWAZA_APP_URL ?? ""),
        };
    }
    const appPathUrl = trimTrailingSlash(env.KAMIWAZA_APP_PATH_URL ?? "");
    const configuredAppUrl = trimTrailingSlash(env.KAMIWAZA_APP_URL ?? "");
    const origin = trimTrailingSlash(env.KAMIWAZA_ORIGIN ?? "");
    return {
        appPathUrl,
        appUrl: appPathUrl || configuredAppUrl || (origin ? `${origin}${appPath}` : ""),
    };
}

function warnOnForwardedPrefix(
    forwardedPrefix: string | null | undefined,
    appPath: string,
): void {
    if (forwardedPrefix == null || appPath === "") {
        return;
    }
    const forwarded = trimTrailingSlash(forwardedPrefix);
    if (forwarded === "" || forwarded === appPath) {
        return;
    }
    // Diagnostics only — the env-derived identity always wins.
    console.warn(
        `[kamiwaza-runtime] x-forwarded-prefix ${JSON.stringify(forwarded)} ` +
            `does not match KAMIWAZA_APP_PATH ${JSON.stringify(appPath)}`,
    );
}

export function getKamiwazaRuntimeServer(
    env: NodeJS.ProcessEnv = process.env,
    forwardedPrefix?: string | null,
): Readonly<KamiwazaRuntimeConfig> {
    const routing = resolveRuntimeRouting(env);
    const urls = resolveAppUrls(env, routing.appPath);
    warnOnForwardedPrefix(forwardedPrefix, routing.appPath);

    return Object.freeze({
        ...routing,
        ...urls,
        deploymentId: env.KAMIWAZA_DEPLOYMENT_ID ?? "",
        appPort: env.KAMIWAZA_APP_PORT ?? "",
    });
}

/**
 * Response body for the scaffold's lazy `/kamiwaza/runtime.json` route.
 * Contains only non-secret deployment routing fields.
 */
export function createRuntimeConfigResponse(
    env: NodeJS.ProcessEnv = process.env,
): Response {
    const runtime = getKamiwazaRuntimeServer(env);
    return new Response(JSON.stringify(runtime), {
        status: 200,
        headers: {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
        },
    });
}
