/**
 * Kamiwaza Forward Auth utilities for Next.js frontends.
 *
 * This module provides the core, server-safe exports.
 * For client components, import from '@kamiwaza/auth/client'.
 * For server components, import from '@kamiwaza/auth/server'.
 * For middleware, import from '@kamiwaza/auth/middleware'.
 *
 * @example
 * // In app/api/[...path]/route.ts - use server exports
 * import { createProxyHandlers } from '@kamiwaza/auth/server';
 *
 * @example
 * // In app/layout.tsx - use client exports
 * import { SessionProvider, AuthGuard } from '@kamiwaza/auth/client';
 *
 * @example
 * // In middleware.ts - use middleware exports
 * import { createAuthMiddleware } from '@kamiwaza/auth/middleware';
 *
 * Note: For backwards compatibility, this index also exports commonly used
 * utilities, but prefer using the subpath imports for cleaner separation.
 */

// Proxy utilities (server-safe)
export { createProxyHandlers, forwardHeaders, buildTargetUrl } from './proxy';
export type { ProxyConfig } from './proxy';

// Fetch utilities (client-safe)
export { apiFetch, createApiFetch } from './fetch';

// Base path utilities (universal)
export {
  withBase,
  getBasePath,
  getBasePathClient,
  BASE_PATH_COOKIE,
  parseCookie,
  normalizeBasePath,
} from './base-path';
