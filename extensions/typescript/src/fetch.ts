/**
 * Client-side fetch utilities with authentication support.
 */

import { getBasePathClient, withBase } from './base-path';

/**
 * Configuration for the API fetch utility.
 */
export interface FetchConfig {
  /** Custom base path (overrides cookie-based detection) */
  basePath?: string;
  /** Whether to include credentials (default: true) */
  includeCredentials?: boolean;
}

/**
 * Build a URL with the appropriate base path.
 *
 * @param path - The API path
 * @param config - Fetch configuration
 * @returns The full URL with base path
 */
const buildUrl = (path: string, config: FetchConfig = {}): string => {
  const base = config.basePath ?? (typeof window === 'undefined' ? '' : getBasePathClient());
  return withBase(path, base);
};

/**
 * Create a configured fetch function for API calls.
 *
 * Returns a fetch wrapper that:
 * - Automatically prepends the App Garden base path
 * - Includes credentials (cookies) by default
 * - Can be configured with a custom base path
 *
 * @param config - Fetch configuration
 * @returns A configured fetch function
 *
 * @example
 * // Create a configured fetch for a specific base path
 * const api = createApiFetch({ basePath: '/runtime/apps/123' });
 * const users = await api('/api/users').then(r => r.json());
 */
export const createApiFetch = (config: FetchConfig = {}) => {
  return (path: string, init?: RequestInit): Promise<Response> => {
    const url = buildUrl(path, config);
    return fetch(url, {
      credentials: config.includeCredentials !== false ? 'include' : 'omit',
      ...init,
    });
  };
};

/**
 * Fetch API with automatic base path handling and credentials.
 *
 * This is a drop-in replacement for fetch() that:
 * - Automatically detects and prepends the App Garden base path
 * - Includes cookies/credentials for authentication
 * - Works in both client and server components
 *
 * @param path - The API path (e.g., '/api/users')
 * @param init - Standard fetch init options
 * @returns A Promise resolving to the Response
 *
 * @example
 * // Simple GET request
 * const response = await apiFetch('/api/users');
 * const users = await response.json();
 *
 * @example
 * // POST request with JSON body
 * const response = await apiFetch('/api/users', {
 *   method: 'POST',
 *   headers: { 'Content-Type': 'application/json' },
 *   body: JSON.stringify({ name: 'John' }),
 * });
 */
export const apiFetch = (path: string, init?: RequestInit): Promise<Response> => {
  return createApiFetch()(path, init);
};
