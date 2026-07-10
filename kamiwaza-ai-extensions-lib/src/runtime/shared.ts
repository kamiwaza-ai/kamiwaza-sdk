/**
 * Shared runtime-path primitives for App Garden path-based routing.
 *
 * The deployment prefix (`KAMIWAZA_APP_PATH`, e.g. `/runtime/apps/<uuid>`)
 * is only known at spawn time. These helpers are the single normalization
 * and join implementation used by the client bootstrap, the server runtime
 * config, the Next config wrapper, and the boot relocator — the grammar
 * here is the relocation contract's grammar.
 */

export type KamiwazaRoutingMode = "path" | "port";

export interface KamiwazaClientRouting {
    routingMode: KamiwazaRoutingMode;
    appPath: string;
}

export interface KamiwazaRuntimeConfig extends KamiwazaClientRouting {
    appUrl: string;
    appPathUrl: string;
    deploymentId: string;
    appPort: string;
}

// Conservative segment alphabet. Deployment paths are platform-generated
// (`/runtime/apps/<uuid>`), so anything outside this set is treated as a
// misconfiguration rather than something to escape: fail closed.
const SEGMENT_RE = /^[A-Za-z0-9._~-]+$/;

/**
 * Normalize an app path: one leading slash, no trailing slashes, validated
 * conservative grammar. Empty-ish input ("" / "/" / null / undefined)
 * normalizes to "" (port mode / no prefix). Throws on invalid input.
 */
export function normalizeAppPath(value: string | null | undefined): string {
    if (value == null) {
        return "";
    }
    const trimmed = value.trim();
    if (trimmed === "" || trimmed === "/") {
        return "";
    }

    const withLeading = trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
    const withoutTrailing = withLeading.replace(/\/+$/, "");

    const segments = withoutTrailing.split("/").slice(1);
    for (const segment of segments) {
        if (
            segment === "" ||
            segment === "." ||
            segment === ".." ||
            segment.includes("%") ||
            !SEGMENT_RE.test(segment)
        ) {
            throw new Error(`invalid app path: ${JSON.stringify(value)}`);
        }
    }

    return withoutTrailing;
}

/**
 * Prefix a root-relative same-app path with the app path. Idempotent and
 * segment-boundary aware; absolute URLs, protocol-relative URLs, and
 * non-root-relative paths are returned untouched.
 */
export function withAppPath(path: string, appPath?: string): string {
    const prefix = normalizeAppPath(appPath);
    if (prefix === "" || path === "") {
        return path;
    }
    // Not root-relative (absolute URL, protocol-relative, or relative path).
    if (!path.startsWith("/") || path.startsWith("//")) {
        return path;
    }
    if (path === prefix || path.startsWith(`${prefix}/`)) {
        return path;
    }
    if (path === "/") {
        return prefix;
    }
    return `${prefix}${path}`;
}
