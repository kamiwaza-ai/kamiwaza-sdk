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

const HTTP_PROTOCOLS = new Set(["http:", "https:"]);
const RAW_USERINFO_RE = /^[\u0000-\u0020]*[a-z][a-z0-9+.-]*:\/\/[^/?#]*@/i;

type AppPathUrlInput = Readonly<{
    value: string;
    appPath: string;
}>;

type PublicUrlInput = Readonly<{
    value: string;
    field: "KAMIWAZA_APP_PATH_URL" | "public app URL for path routing";
}>;

type ForwardedPrefixInput = Readonly<{
    forwardedPrefix: string | null | undefined;
    appPath: string;
}>;

function trimTrailingSlash(value: string): string {
    return value.replace(/\/+$/, "");
}

function invalidPublicUrl(input: PublicUrlInput): Error {
    return new Error(
        `invalid ${input.field}: ${JSON.stringify(input.value)}`,
    );
}

function parsePublicHttpUrl(input: PublicUrlInput): URL {
    // Keep the Python and WHATWG parsers from selecting different hosts for
    // ambiguous inputs such as `https://example.com\@evil.com`.
    if (input.value.includes("\\")) {
        throw invalidPublicUrl(input);
    }
    let parsed: URL;
    try {
        parsed = new URL(input.value);
    } catch {
        throw invalidPublicUrl(input);
    }
    if (!HTTP_PROTOCOLS.has(parsed.protocol)) {
        throw invalidPublicUrl(input);
    }
    return parsed;
}

function originWithAppPath(input: AppPathUrlInput): string {
    const parsed = parsePublicHttpUrl({
        value: input.value,
        field: "public app URL for path routing",
    });
    return `${parsed.origin}${input.appPath}`;
}

function hasUnexpectedUrlComponents(parsed: URL): boolean {
    return [parsed.username, parsed.password, parsed.search, parsed.hash].some(
        (component) => component !== "",
    );
}

function normalizeAppPathUrl(input: AppPathUrlInput): string {
    const parsed = parsePublicHttpUrl({
        value: input.value,
        field: "KAMIWAZA_APP_PATH_URL",
    });
    if (RAW_USERINFO_RE.test(input.value)) {
        throw new Error(
            `KAMIWAZA_APP_PATH_URL must be the public origin plus ` +
                `KAMIWAZA_APP_PATH: ${JSON.stringify(input.value)}`,
        );
    }
    if (hasUnexpectedUrlComponents(parsed)) {
        throw new Error(
            `KAMIWAZA_APP_PATH_URL must be the public origin plus ` +
                `KAMIWAZA_APP_PATH: ${JSON.stringify(input.value)}`,
        );
    }
    if (trimTrailingSlash(parsed.pathname) !== input.appPath) {
        throw new Error(
            `KAMIWAZA_APP_PATH_URL must be the public origin plus ` +
                `KAMIWAZA_APP_PATH: ${JSON.stringify(input.value)}`,
        );
    }
    return `${parsed.origin}${input.appPath}`;
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
    const configuredAppPathUrl = trimTrailingSlash(
        env.KAMIWAZA_APP_PATH_URL ?? "",
    );
    const appPathUrl = configuredAppPathUrl
        ? normalizeAppPathUrl({ value: configuredAppPathUrl, appPath })
        : "";
    const configuredAppUrl = trimTrailingSlash(env.KAMIWAZA_APP_URL ?? "");
    const origin = trimTrailingSlash(env.KAMIWAZA_ORIGIN ?? "");
    const publicOrigin = configuredAppUrl || origin;
    return {
        appPathUrl,
        appUrl:
            appPathUrl ||
            (publicOrigin
                ? originWithAppPath({ value: publicOrigin, appPath })
                : ""),
    };
}

function warnOnForwardedPrefix(input: ForwardedPrefixInput): void {
    if (input.forwardedPrefix == null) {
        return;
    }
    if (input.appPath === "") {
        return;
    }
    const forwarded = trimTrailingSlash(input.forwardedPrefix);
    if (forwarded === "") {
        return;
    }
    if (forwarded === input.appPath) {
        return;
    }
    // Diagnostics only — the env-derived identity always wins.
    console.warn(
        `[kamiwaza-runtime] x-forwarded-prefix ${JSON.stringify(forwarded)} ` +
            `does not match KAMIWAZA_APP_PATH ${JSON.stringify(input.appPath)}`,
    );
}

export function getKamiwazaRuntimeServer(
    env: NodeJS.ProcessEnv = process.env,
    forwardedPrefix?: string | null,
): Readonly<KamiwazaRuntimeConfig> {
    const routing = resolveRuntimeRouting(env);
    const urls = resolveAppUrls(env, routing.appPath);
    warnOnForwardedPrefix({ forwardedPrefix, appPath: routing.appPath });

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
