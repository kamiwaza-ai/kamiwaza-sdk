/** Canonical stdlib-only runtime path and routing-mode contract. */

const SENTINEL_FAMILY_RE = /__KZ_RUNTIME_BASE_[0-9A-F]+__/;
const SEGMENT_RE = /^[A-Za-z0-9_-]+$/;
const CONTROL_RE = /[\u0000-\u001f\u007f]/;
const INVALID_SEGMENTS = new Set(["", ".", "." + "."]);
const MAX_PATH_LENGTH = 512;
const MAX_SEGMENT_LENGTH = 128;

function trimAsciiSpaces(value) {
    return value.replace(/^ +| +$/g, "");
}

function isEmptyRuntimePath(value) {
    if (value == null) {
        return true;
    }
    assertRuntimePathString(value);
    return trimAsciiSpaces(value).replace(/\/+$/, "") === "";
}

function assertRuntimePathString(value) {
    if (typeof value !== "string") {
        throw new Error("runtime path is empty");
    }
}

function assertRuntimePathCharacters(value) {
    if (CONTROL_RE.test(value)) {
        throw new Error("runtime path contains control characters");
    }
    if (value.includes("\\")) {
        throw new Error(`runtime path contains forbidden characters: ${JSON.stringify(value)}`);
    }
    if (value.includes("?")) {
        throw new Error(`runtime path contains forbidden characters: ${JSON.stringify(value)}`);
    }
    if (value.includes("#")) {
        throw new Error(`runtime path contains forbidden characters: ${JSON.stringify(value)}`);
    }
}

function assertRuntimePathLength(value) {
    if (value.length > MAX_PATH_LENGTH) {
        throw new Error(`runtime path exceeds maximum length ${MAX_PATH_LENGTH}`);
    }
}

function assertRuntimeSegment(segment) {
    if (INVALID_SEGMENTS.has(segment)) {
        throw new Error(`invalid runtime path segment: ${JSON.stringify(segment)}`);
    }
    if (segment.includes("%")) {
        throw new Error(`invalid runtime path segment: ${JSON.stringify(segment)}`);
    }
    if (!SEGMENT_RE.test(segment)) {
        throw new Error(`invalid runtime path segment: ${JSON.stringify(segment)}`);
    }
    if (segment.length > MAX_SEGMENT_LENGTH) {
        throw new Error(`runtime path segment exceeds maximum length ${MAX_SEGMENT_LENGTH}`);
    }
}

/** Validate and normalize one nonempty runtime deployment path. */
export function validateRuntimePath(value) {
    assertRuntimePathString(value);
    assertRuntimePathCharacters(value);
    const trimmed = trimAsciiSpaces(value);
    if (trimmed === "") {
        throw new Error("runtime path is empty");
    }
    assertRuntimePathLength(trimmed);
    if (SENTINEL_FAMILY_RE.test(trimmed)) {
        throw new Error("runtime path must not contain the relocation sentinel");
    }
    const withLeading = trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
    const withoutTrailing = withLeading.replace(/\/+$/, "");
    if (withoutTrailing === "") {
        throw new Error("runtime path normalizes to empty");
    }
    for (const segment of withoutTrailing.split("/").slice(1)) {
        assertRuntimeSegment(segment);
    }
    return withoutTrailing;
}

/** Resolve explicit or legacy-inferred routing mode from raw environment values. */
export function resolveRoutingMode(env) {
    const mode = env.KAMIWAZA_ROUTING_MODE;
    if (mode === "port") {
        return { routingMode: "port", appPath: "" };
    }
    if (mode === "path") {
        return { routingMode: "path", appPath: validateRuntimePath(env.KAMIWAZA_APP_PATH) };
    }
    if (mode != null && mode !== "") {
        throw new Error(`unknown KAMIWAZA_ROUTING_MODE: ${JSON.stringify(mode)}`);
    }
    const rawPath = env.KAMIWAZA_APP_PATH;
    if (isEmptyRuntimePath(rawPath)) {
        return { routingMode: "port", appPath: "" };
    }
    return { routingMode: "path", appPath: validateRuntimePath(rawPath) };
}
