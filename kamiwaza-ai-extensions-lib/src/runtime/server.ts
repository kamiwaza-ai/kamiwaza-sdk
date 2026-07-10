/**
 * Server-side runtime config resolution.
 *
 * Environment is authoritative for deployment identity: `x-forwarded-prefix`
 * may be compared for diagnostics but never selects the path. Precedence in
 * path mode: KAMIWAZA_APP_PATH_URL, then KAMIWAZA_APP_URL, then
 * KAMIWAZA_ORIGIN + appPath. Port mode uses KAMIWAZA_APP_URL.
 */

import {
    type KamiwazaRoutingMode,
    type KamiwazaRuntimeConfig,
    normalizeAppPath,
} from "./shared";

function trimTrailingSlash(value: string): string {
    return value.replace(/\/+$/, "");
}

function resolveRoutingMode(env: NodeJS.ProcessEnv): KamiwazaRoutingMode {
    const mode = env.KAMIWAZA_ROUTING_MODE;
    if (mode === "path" || mode === "port") {
        return mode;
    }
    return (env.KAMIWAZA_APP_PATH ?? "").trim() !== "" ? "path" : "port";
}

export function getKamiwazaRuntimeServer(
    env: NodeJS.ProcessEnv = process.env,
    forwardedPrefix?: string | null,
): Readonly<KamiwazaRuntimeConfig> {
    const routingMode = resolveRoutingMode(env);

    let appPath = "";
    let appPathUrl = "";
    let appUrl = "";

    if (routingMode === "path") {
        appPath = normalizeAppPath(env.KAMIWAZA_APP_PATH);
        if (appPath === "") {
            throw new Error("path routing mode requires a nonempty KAMIWAZA_APP_PATH");
        }

        const configuredPathUrl = trimTrailingSlash(env.KAMIWAZA_APP_PATH_URL ?? "");
        const configuredAppUrl = trimTrailingSlash(env.KAMIWAZA_APP_URL ?? "");
        const origin = trimTrailingSlash(env.KAMIWAZA_ORIGIN ?? "");

        appPathUrl = configuredPathUrl;
        appUrl =
            configuredPathUrl ||
            configuredAppUrl ||
            (origin ? `${origin}${appPath}` : "");
    } else {
        appUrl = trimTrailingSlash(env.KAMIWAZA_APP_URL ?? "");
    }

    if (forwardedPrefix != null && routingMode === "path") {
        const forwarded = trimTrailingSlash(forwardedPrefix);
        if (forwarded !== "" && forwarded !== appPath) {
            // Diagnostics only — the env-derived identity always wins.
            console.warn(
                `[kamiwaza-runtime] x-forwarded-prefix ${JSON.stringify(forwarded)} ` +
                    `does not match KAMIWAZA_APP_PATH ${JSON.stringify(appPath)}`,
            );
        }
    }

    return Object.freeze({
        routingMode,
        appPath,
        appPathUrl,
        appUrl,
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
