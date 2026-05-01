/**
 * Single source of truth for the platform's forwarded-auth envelope
 * headers. Both `server/proxy.ts` (forward FROM Next.js TO the
 * extension backend) and `local-dev-auth/index.ts` (clear inbound
 * spoofs BEFORE injecting bridge-synthesized values) consume this
 * list.
 *
 * Background: PR #87 round-10 review caught a maintainability gap —
 * if a future envelope header (e.g. `x-user-tenant-id`) is added to
 * one list but not the other, an attacker on the dev server could
 * spoof it (because the bridge wouldn't clear it). Importing from a
 * shared constant eliminates the drift surface.
 *
 * Headers that are NOT auth-bearing (`cookie`, `content-type`,
 * `x-request-id`) are intentionally excluded — those belong only on
 * the proxy's forward-list (they have no role in the bridge's
 * clear-and-synthesize cycle).
 */
export const ENVELOPE_AUTH_HEADERS = [
    "authorization",
    "x-auth-token",
    "x-user-id",
    "x-user-email",
    "x-user-name",
    "x-user-roles",
    "x-user-system-high",
    "x-workroom-id",
    "x-user-workroom-id",
    "x-user-workroom-role",
    "x-user-signature",
    "x-user-signature-ts",
] as const;
