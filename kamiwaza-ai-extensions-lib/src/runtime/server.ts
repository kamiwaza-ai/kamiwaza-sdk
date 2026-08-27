/**
 * Server-side runtime config resolution.
 *
 * Environment is authoritative for deployment identity: `x-forwarded-prefix`
 * may be compared for diagnostics but never selects the path. Precedence in
 * Path mode uses KAMIWAZA_APP_PATH_URL when explicit; otherwise it derives
 * origin(KAMIWAZA_APP_URL || KAMIWAZA_ORIGIN) + appPath so an old port-style
 * URL cannot silently drop the deployment prefix. Port mode uses APP_URL.
 */

import {
    type KamiwazaRuntimeConfig,
    resolveRuntimeRouting,
} from "./shared";

function trimTrailingSlash(value: string): string {
    return value.replace(/\/+$/, "");
}

function originWithAppPath(value: string, appPath: string): string {
    let parsed: URL;
    try {
        parsed = new URL(value);
    } catch {
        throw new Error(
            `invalid public app URL for path routing: ${JSON.stringify(value)}`,
        );
    }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        throw new Error(
            `invalid public app URL for path routing: ${JSON.stringify(value)}`,
        );
    }
    return `${parsed.origin}${appPath}`;
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
    const publicOrigin = configuredAppUrl || origin;
    return {
        appPathUrl,
        appUrl:
            appPathUrl ||
            (publicOrigin ? originWithAppPath(publicOrigin, appPath) : ""),
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
