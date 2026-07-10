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
const CONTROL_RE = /[\u0000-\u001f\u007f]/;
const MAX_PATH_LENGTH = 512;
const MAX_SEGMENT_LENGTH = 128;

/**
 * Normalize an app path: one leading slash, no trailing slashes, validated
 * conservative grammar, bounded length. Empty-ish input ("" / "/" / null /
 * undefined) normalizes to "" (port mode / no prefix). Throws on invalid
 * input — including raw control characters BEFORE trimming (a trailing
 * newline in an env value is a misconfiguration, not whitespace).
 */
export function normalizeAppPath(value: string | null | undefined): string {
    if (value == null) {
        return "";
    }
    if (CONTROL_RE.test(value)) {
        throw new Error(`invalid app path (control characters): ${JSON.stringify(value)}`);
    }
    const trimmed = value.trim();
    if (trimmed === "" || trimmed === "/") {
        return "";
    }
    if (trimmed.length > MAX_PATH_LENGTH) {
        throw new Error(`invalid app path (exceeds maximum length ${MAX_PATH_LENGTH})`);
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
            segment.length > MAX_SEGMENT_LENGTH ||
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
    // Already-prefixed check is boundary-aware: `/`, `?`, `#`, or
    // end-of-string after the exact prefix all count (a longer segment like
    // `<prefix>beef/...` is a DIFFERENT deployment and still gets joined).
    if (path === prefix) {
        return path;
    }
    if (path.startsWith(prefix)) {
        const boundary = path[prefix.length];
        if (boundary === "/" || boundary === "?" || boundary === "#") {
            return path;
        }
    }
    if (path === "/") {
        return prefix;
    }
    return `${prefix}${path}`;
}
