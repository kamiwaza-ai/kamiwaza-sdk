/**
 * Base path utilities for App Garden deployments.
 *
 * App Garden deploys applications at dynamic paths like /runtime/apps/{id}/.
 * These utilities help handle path prefixing for client-side navigation and API calls.
 */

/**
 * Cookie name used to store the base path.
 * Set by the platform when deploying to App Garden.
 */
export const BASE_PATH_COOKIE = 'app-base-path';

/**
 * Normalize a base path value.
 *
 * @param value - The raw base path value
 * @returns Normalized path without trailing slashes, or empty string
 *
 * @example
 * normalizeBasePath('/runtime/apps/123/') // '/runtime/apps/123'
 * normalizeBasePath(null) // ''
 */
export const normalizeBasePath = (value?: string | null): string => {
  if (!value) return '';
  const trimmed = value.trim();
  if (!trimmed || trimmed === '/') return '';
  return trimmed.replace(/\/+$/, '');
};

/**
 * Parse a specific cookie value from a cookie header string.
 *
 * @param cookieHeader - The full cookie header string
 * @param name - The cookie name to extract
 * @returns The cookie value, or empty string if not found
 *
 * @example
 * parseCookie('foo=bar; app-base-path=%2Fruntime%2Fapps%2F123', 'app-base-path')
 * // Returns: '/runtime/apps/123'
 */
export const parseCookie = (cookieHeader: string | undefined, name: string): string => {
  if (!cookieHeader) return '';
  const match = cookieHeader.match(new RegExp(`${name}=([^;]+)`));
  return match ? decodeURIComponent(match[1]) : '';
};

/**
 * Prepend a base path to a URL path.
 *
 * Handles edge cases like:
 * - Empty base paths
 * - Paths that already include the base
 * - Double slashes
 *
 * @param path - The path to prefix
 * @param basePath - The base path to prepend
 * @returns The combined path
 *
 * @example
 * withBase('/api/users', '/runtime/apps/123')
 * // Returns: '/runtime/apps/123/api/users'
 *
 * withBase('/runtime/apps/123/api/users', '/runtime/apps/123')
 * // Returns: '/runtime/apps/123/api/users' (no double prefix)
 */
export const withBase = (path: string, basePath?: string): string => {
  if (!path) return path;
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  const normalizedBase = normalizeBasePath(basePath);
  if (!normalizedBase) return normalizedPath;
  if (normalizedPath.startsWith(normalizedBase)) return normalizedPath;
  return `${normalizedBase}${normalizedPath}`.replace(/\/{2,}/g, '/');
};

/**
 * Get the base path on the server side from request headers.
 *
 * @param cookieHeader - The cookie header from the request
 * @returns The normalized base path
 *
 * @example
 * // In a server component or API route
 * import { headers } from 'next/headers';
 * const basePath = getBasePath(headers().get('cookie'));
 */
export const getBasePath = (cookieHeader: string | undefined): string => {
  return normalizeBasePath(parseCookie(cookieHeader, BASE_PATH_COOKIE));
};

/**
 * Get the base path on the client side from document.cookie.
 *
 * @returns The normalized base path, or empty string if not in browser
 *
 * @example
 * // In a client component
 * const basePath = getBasePathClient();
 * const apiUrl = `${basePath}/api/users`;
 */
export const getBasePathClient = (): string => {
  if (typeof document === 'undefined') return '';
  return normalizeBasePath(parseCookie(document.cookie, BASE_PATH_COOKIE));
};
