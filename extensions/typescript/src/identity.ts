/**
 * Server-side identity utilities for Kamiwaza ForwardAuth.
 *
 * These utilities handle extraction and normalization of user identity
 * from Traefik ForwardAuth headers, including proper UUID handling
 * for cases where the x-user-id header contains a username instead of UUID.
 *
 * @example
 * // In a Next.js API route or server component
 * import { headers } from 'next/headers';
 * import { extractIdentity, getOrCreateUserUuid } from '@kamiwaza/auth/server';
 *
 * const headersList = await headers();
 * const identity = extractIdentity(headersList);
 *
 * if (identity) {
 *   // Use identity.resolvedUuid for database operations
 *   await db.upsertUser({ id: identity.resolvedUuid, email: identity.email });
 * }
 */

import { v5 as uuidv5 } from 'uuid';

/**
 * UUID regex pattern for validation.
 */
const UUID_REGEX =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Kamiwaza namespace UUID for generating deterministic UUIDs from usernames.
 * Using the DNS namespace as a stable, well-known namespace.
 */
const KAMIWAZA_NAMESPACE = '6ba7b810-9dad-11d1-80b4-00c04fd430c8';

/**
 * User identity extracted from ForwardAuth headers.
 */
export interface KamiwazaIdentity {
  /** Raw user ID from x-user-id header (may be username or UUID) */
  userId: string;
  /** User email from x-user-email header */
  email: string;
  /** User display name from x-user-name header */
  name: string;
  /** User roles from x-user-roles header */
  roles: string[];
  /** Request ID from x-request-id header */
  requestId: string | null;
  /** Resolved UUID - always a valid UUID format, safe for database operations */
  resolvedUuid: string;
}

/**
 * Check if a string is a valid UUID format.
 *
 * @param value - The string to validate
 * @returns True if the string is a valid UUID
 *
 * @example
 * isValidUuid('admin') // false
 * isValidUuid('550e8400-e29b-41d4-a716-446655440000') // true
 */
export function isValidUuid(value: string): boolean {
  return UUID_REGEX.test(value);
}

/**
 * Generate a deterministic UUID from a non-UUID user identifier.
 *
 * Uses UUID v5 (SHA-1 based) with the Kamiwaza namespace to generate
 * a consistent UUID for any given username. The same username will
 * always produce the same UUID.
 *
 * @param identifier - The non-UUID identifier (e.g., username)
 * @returns A valid UUID v5 derived from the identifier
 *
 * @example
 * generateUuidFromIdentifier('admin')
 * // Returns: 'a1b2c3d4-e5f6-5a7b-8c9d-0e1f2a3b4c5d' (example)
 */
export function generateUuidFromIdentifier(identifier: string): string {
  return uuidv5(identifier, KAMIWAZA_NAMESPACE);
}

/**
 * Resolve a user ID to a valid UUID.
 *
 * If the user ID is already a valid UUID, returns it unchanged.
 * Otherwise, generates a deterministic UUID from the identifier.
 *
 * @param userId - The user ID to resolve (may be UUID or username)
 * @returns A valid UUID
 *
 * @example
 * resolveUserUuid('550e8400-e29b-41d4-a716-446655440000')
 * // Returns: '550e8400-e29b-41d4-a716-446655440000' (unchanged)
 *
 * resolveUserUuid('admin')
 * // Returns: generated UUID v5 based on 'admin'
 */
export function resolveUserUuid(userId: string): string {
  if (isValidUuid(userId)) {
    return userId;
  }
  return generateUuidFromIdentifier(userId);
}

/**
 * Header names used by Kamiwaza ForwardAuth.
 */
export const FORWARD_AUTH_HEADERS = {
  USER_ID: 'x-user-id',
  USER_EMAIL: 'x-user-email',
  USER_NAME: 'x-user-name',
  USER_ROLES: 'x-user-roles',
  REQUEST_ID: 'x-request-id',
} as const;

/**
 * Extract user identity from ForwardAuth headers.
 *
 * Returns null if required headers (user ID and email) are missing.
 * The resolved UUID is always a valid UUID format, either from the
 * original x-user-id header or generated from the username.
 *
 * @param headers - Headers object (from next/headers or Request.headers)
 * @returns KamiwazaIdentity if authenticated, null otherwise
 *
 * @example
 * // In a Next.js server component or API route
 * import { headers } from 'next/headers';
 *
 * const headersList = await headers();
 * const identity = extractIdentity(headersList);
 *
 * if (identity) {
 *   console.log(`User: ${identity.email}, UUID: ${identity.resolvedUuid}`);
 * }
 */
export function extractIdentity(
  headers: Headers | { get(name: string): string | null }
): KamiwazaIdentity | null {
  const userId = headers.get(FORWARD_AUTH_HEADERS.USER_ID);
  const email = headers.get(FORWARD_AUTH_HEADERS.USER_EMAIL);

  // Both user ID and email are required for a valid identity
  if (!userId || !email) {
    return null;
  }

  const name = headers.get(FORWARD_AUTH_HEADERS.USER_NAME) || 'User';
  const rolesHeader = headers.get(FORWARD_AUTH_HEADERS.USER_ROLES);
  const roles = rolesHeader ? rolesHeader.split(',').map((r) => r.trim()) : [];
  const requestId = headers.get(FORWARD_AUTH_HEADERS.REQUEST_ID);

  return {
    userId,
    email,
    name,
    roles,
    requestId,
    resolvedUuid: resolveUserUuid(userId),
  };
}

/**
 * Get the resolved UUID for database operations.
 *
 * This is a convenience function that extracts the identity and returns
 * only the resolved UUID. Returns null if not authenticated.
 *
 * @param headers - Headers object (from next/headers or Request.headers)
 * @returns Resolved UUID if authenticated, null otherwise
 *
 * @example
 * const uuid = getResolvedUserUuid(headersList);
 * if (uuid) {
 *   await db.query('SELECT * FROM users WHERE id = $1', [uuid]);
 * }
 */
export function getResolvedUserUuid(
  headers: Headers | { get(name: string): string | null }
): string | null {
  const identity = extractIdentity(headers);
  return identity?.resolvedUuid ?? null;
}
