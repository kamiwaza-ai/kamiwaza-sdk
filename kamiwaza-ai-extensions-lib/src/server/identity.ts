import type { Identity } from "./types";

/**
 * Extract user identity from platform-injected request headers.
 *
 * Works with both the standard `Headers` object and Next.js
 * `ReadonlyHeaders` from `next/headers`.
 *
 * Returns `null` when no identity headers are present.
 */
export function extractIdentity(
    headers: Headers | { get(name: string): string | null }
): Identity | null {
    const userId = headers.get("x-user-id");
    if (!userId) return null;

    const rolesRaw = headers.get("x-user-roles") ?? "";
    const roles = rolesRaw
        .split(",")
        .map((r) => r.trim())
        .filter(Boolean);

    return {
        userId,
        email: headers.get("x-user-email"),
        name: headers.get("x-user-name"),
        roles,
        workroomId: headers.get("x-workroom-id"),
        isAuthenticated: true,
    };
}
