import type { Identity } from "./types";
import { MisboundAuthError } from "./errors";

type HeadersLike = Headers | { get(name: string): string | null };

function readStripped(headers: HeadersLike, name: string): string | null {
    const raw = headers.get(name);
    if (raw === null || raw === undefined) return null;
    const trimmed = raw.trim();
    return trimmed.length === 0 ? null : trimmed;
}

function parseRoles(headers: HeadersLike): string[] {
    const raw = headers.get("x-user-roles");
    if (!raw) return [];
    return raw
        .split(",")
        .map((r) => r.trim())
        .filter(Boolean);
}

function projectFields(headers: HeadersLike, userId: string, workroomId: string | null): Identity {
    return {
        userId,
        email: readStripped(headers, "x-user-email"),
        name: readStripped(headers, "x-user-name"),
        roles: parseRoles(headers),
        systemHigh: readStripped(headers, "x-user-system-high"),
        workroomId,
        workroomRole: readStripped(headers, "x-user-workroom-role"),
        requestId: readStripped(headers, "x-request-id"),
        isAuthenticated: true,
    };
}

/**
 * Permissive header parsing — never throws. Returns ``null`` when no
 * ``X-User-Id`` header is present (caller wants to handle local-dev /
 * unauthenticated cases itself).
 *
 * Backwards-compatible with v0.2 callers; v0.3 adds the missing
 * ``systemHigh`` / ``workroomRole`` / ``requestId`` fields to the returned
 * shape, which previously held only the v0.2 subset.
 */
export function extractIdentity(headers: HeadersLike): Identity | null {
    const userId = readStripped(headers, "x-user-id");
    if (!userId) return null;
    const workroomId = readStripped(headers, "x-workroom-id");
    return projectFields(headers, userId, workroomId);
}

/**
 * Strict header parsing — throws ``MisboundAuthError`` when ``X-User-Id``
 * or ``X-Workroom-Id`` is missing or whitespace-only.
 *
 * Mirrors Python's ``kamiwaza_extensions_lib.identity.extract_identity``.
 * The same canonical test vectors at
 * ``docs/extensions/non-sdk-flow/test-vectors.json`` exercise this path.
 */
export function extractIdentityStrict(headers: HeadersLike): Identity {
    const userId = readStripped(headers, "x-user-id");
    if (!userId) {
        throw new MisboundAuthError("Required envelope header X-User-Id missing or empty");
    }
    const workroomId = readStripped(headers, "x-workroom-id");
    if (!workroomId) {
        throw new MisboundAuthError("Required envelope header X-Workroom-Id missing or empty");
    }
    return projectFields(headers, userId, workroomId);
}
