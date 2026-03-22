/**
 * Server-side exports for Kamiwaza auth.
 *
 * This module exports server-side utilities that use next/headers.
 * Import from '@kamiwaza/auth/server' in server components or API routes.
 *
 * @example
 * // In a Server Component
 * import { getBasePathServer } from '@kamiwaza/auth/server';
 *
 * export default async function Page() {
 *   const basePath = await getBasePathServer();
 *   return <a href={`${basePath}/dashboard`}>Dashboard</a>;
 * }
 *
 * @example
 * // Identity extraction with UUID resolution
 * import { headers } from 'next/headers';
 * import { extractIdentity } from '@kamiwaza/auth/server';
 *
 * const headersList = await headers();
 * const identity = extractIdentity(headersList);
 *
 * if (identity) {
 *   // identity.resolvedUuid is always a valid UUID
 *   await db.upsertUser({ id: identity.resolvedUuid, email: identity.email });
 * }
 */

// Server-side base path utilities
export { getBasePathServer, getRequestBasePath } from './base-path-server';

// Proxy utilities (work in route handlers)
export { createProxyHandlers, forwardHeaders, buildTargetUrl } from './proxy';
export type { ProxyConfig } from './proxy';

// Identity utilities for ForwardAuth
export {
  extractIdentity,
  getResolvedUserUuid,
  isValidUuid,
  resolveUserUuid,
  generateUuidFromIdentifier,
  FORWARD_AUTH_HEADERS,
} from './identity';
export type { KamiwazaIdentity } from './identity';
