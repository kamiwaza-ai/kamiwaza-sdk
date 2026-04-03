import type { Identity } from "./types";
import { getLocalDevAuthHeaders } from "./localDevAuth";

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
    const bridgeHeaders = getLocalDevAuthHeaders();
    const userId = headers.get("x-user-id") ?? bridgeHeaders["x-user-id"] ?? null;
    if (!userId) return null;

    const rolesRaw = headers.get("x-user-roles") ?? bridgeHeaders["x-user-roles"] ?? "";
    const roles = rolesRaw
        .split(",")
        .map((r) => r.trim())
        .filter(Boolean);

    return {
        userId,
        email: headers.get("x-user-email") ?? bridgeHeaders["x-user-email"] ?? null,
        name: headers.get("x-user-name") ?? bridgeHeaders["x-user-name"] ?? null,
        roles,
        workroomId: headers.get("x-workroom-id") ?? bridgeHeaders["x-workroom-id"] ?? null,
        isAuthenticated: true,
    };
}
