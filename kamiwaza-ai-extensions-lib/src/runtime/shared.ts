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
const SEGMENT_RE = /^[A-Za-z0-9_-]+$/;
const CONTROL_RE = /[\u0000-\u001f\u007f]/;
const SENTINEL_FAMILY_RE = /__KZ_RUNTIME_BASE_[0-9A-F]+__/;
const INVALID_SEGMENTS = new Set(["", ".", "." + "."]);
const PREFIX_BOUNDARIES = new Set(["/", "?", "#"]);
const EMPTY_PATHS = new Set(["", "/"]);
const MAX_PATH_LENGTH = 512;
const MAX_SEGMENT_LENGTH = 128;

interface RoutingEnvironment {
    readonly [name: string]: string | undefined;
    readonly KAMIWAZA_ROUTING_MODE?: string;
    readonly KAMIWAZA_APP_PATH?: string;
}

function trimAsciiSpaces(value: string): string {
    return value.replace(/^ +| +$/g, "");
}

function isInvalidSegment(segment: string): boolean {
    if (INVALID_SEGMENTS.has(segment)) {
        return true;
    }
    if (segment.includes("%")) {
        return true;
    }
    if (segment.length > MAX_SEGMENT_LENGTH) {
        return true;
    }
    return !SEGMENT_RE.test(segment);
}

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
    // Only U+0020 is ignorable. Other Unicode whitespace is outside the
    // conservative ASCII path grammar and must fail identically in JS/Python.
    const trimmed = trimAsciiSpaces(value);
    if (EMPTY_PATHS.has(trimmed)) {
        return "";
    }
    if (trimmed.length > MAX_PATH_LENGTH) {
        throw new Error(`invalid app path (exceeds maximum length ${MAX_PATH_LENGTH})`);
    }

    const withLeading = trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
    const withoutTrailing = withLeading.replace(/\/+$/, "");

    const invalidSegment = withoutTrailing.split("/").slice(1).find(isInvalidSegment);
    if (invalidSegment !== undefined) {
        throw new Error(`invalid app path: ${JSON.stringify(value)}`);
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
    if (prefix === "") {
        return path;
    }
    if (path === "") {
        return path;
    }
    // Not root-relative (absolute URL, protocol-relative, or relative path).
    if (!path.startsWith("/")) {
        return path;
    }
    if (path.startsWith("//")) {
        return path;
    }
    // Already-prefixed check is boundary-aware: `/`, `?`, `#`, or
    // end-of-string after the exact prefix all count (a longer segment like
    // `<prefix>beef/...` is a DIFFERENT deployment and still gets joined).
    if (path === prefix) {
        return path;
    }
    if (hasPrefixBoundary(path, prefix)) {
        return path;
    }
    if (path === "/") {
        return prefix;
    }
    return `${prefix}${path}`;
}

function hasPrefixBoundary(path: string, prefix: string): boolean {
    if (!path.startsWith(prefix)) {
        return false;
    }
    return PREFIX_BOUNDARIES.has(path[prefix.length]);
}

function resolvePathRouting(rawPath: string | undefined): KamiwazaClientRouting {
    const appPath = normalizeAppPath(rawPath);
    if (appPath === "") {
        throw new Error("path routing mode requires a nonempty KAMIWAZA_APP_PATH");
    }
    if (SENTINEL_FAMILY_RE.test(appPath)) {
        throw new Error("KAMIWAZA_APP_PATH must not contain the relocation sentinel");
    }
    return { routingMode: "path", appPath };
}

/** Resolve the canonical deployment routing identity from raw environment values. */
export function resolveRuntimeRouting(env: RoutingEnvironment): KamiwazaClientRouting {
    const mode = env.KAMIWAZA_ROUTING_MODE;
    if (mode === "port") {
        return { routingMode: "port", appPath: "" };
    }
    if (mode === "path") {
        return resolvePathRouting(env.KAMIWAZA_APP_PATH);
    }
    if (mode != null && mode !== "") {
        throw new Error(`unknown KAMIWAZA_ROUTING_MODE: ${JSON.stringify(mode)}`);
    }
    const appPath = normalizeAppPath(env.KAMIWAZA_APP_PATH);
    return appPath === ""
        ? { routingMode: "port", appPath: "" }
        : resolvePathRouting(appPath);
}
