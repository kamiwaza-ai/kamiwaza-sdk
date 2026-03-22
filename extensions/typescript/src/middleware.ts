/**
 * Next.js middleware for App Garden path-based routing.
 *
 * Handles:
 * 1. Capturing x-forwarded-prefix into a session cookie
 * 2. Rewriting incoming requests to strip the prefix for Next.js routing
 */

import { NextResponse, type NextRequest } from 'next/server';
import { BASE_PATH_COOKIE } from './base-path';

/**
 * Configuration options for the auth middleware.
 */
export interface MiddlewareConfig {
  /** Cookie name for storing base path (default: 'app-base-path') */
  cookieName?: string;
  /** Environment variable for static base path (default: 'KAMIWAZA_APP_PATH') */
  envVarName?: string;
  /** Cookie options */
  cookieOptions?: {
    sameSite?: 'strict' | 'lax' | 'none';
    secure?: boolean;
    httpOnly?: boolean;
  };
}

const DEFAULT_CONFIG: Required<MiddlewareConfig> = {
  cookieName: BASE_PATH_COOKIE,
  // Use KAMIWAZA_APP_PATH - system-provided by App Garden at runtime
  // NEXT_PUBLIC_ vars are replaced at build time, not available at runtime
  envVarName: 'KAMIWAZA_APP_PATH',
  cookieOptions: {
    sameSite: 'lax',
    secure: true,
    httpOnly: false,
  },
};

/**
 * Create an auth middleware function for Next.js.
 *
 * The middleware:
 * 1. Captures the base path from x-forwarded-prefix header or env var
 * 2. Stores it in a session cookie for client-side access
 * 3. Rewrites requests to strip the prefix so Next.js can route correctly
 *
 * @param config - Optional middleware configuration
 * @returns Middleware function
 *
 * @example
 * // middleware.ts
 * import { createAuthMiddleware, DEFAULT_MIDDLEWARE_MATCHER } from '@kamiwaza/auth/middleware';
 *
 * export const middleware = createAuthMiddleware();
 *
 * export const config = {
 *   matcher: DEFAULT_MIDDLEWARE_MATCHER,
 * };
 *
 * @example
 * // With custom options
 * export const middleware = createAuthMiddleware({
 *   cookieOptions: { secure: false }, // For local development
 * });
 */
export function createAuthMiddleware(config: MiddlewareConfig = {}) {
  const mergedConfig = {
    ...DEFAULT_CONFIG,
    ...config,
    cookieOptions: { ...DEFAULT_CONFIG.cookieOptions, ...config.cookieOptions },
  };

  return function middleware(request: NextRequest): NextResponse {
    // Get prefix from header (Traefik) or environment variable
    const configuredPrefix = process.env[mergedConfig.envVarName];
    const headerPrefix = request.headers.get('x-forwarded-prefix');
    const prefix = headerPrefix || configuredPrefix || '';

    const response = NextResponse.next();

    // Manage the base path cookie
    if (prefix && prefix !== '/') {
      // Set session cookie (no maxAge = expires when browser closes)
      // Cookie is refreshed on every navigation through middleware
      response.cookies.set(mergedConfig.cookieName, prefix, {
        path: '/',
        ...mergedConfig.cookieOptions,
      });
    } else if (!prefix && request.cookies.has(mergedConfig.cookieName)) {
      // Clear cookie if no prefix
      response.cookies.delete(mergedConfig.cookieName);
    }

    // Rewrite path-based routes to root so Next.js can serve pages
    if (prefix && request.nextUrl.pathname.startsWith(prefix)) {
      const url = request.nextUrl.clone();
      url.pathname = url.pathname.slice(prefix.length) || '/';
      return NextResponse.rewrite(url, {
        headers: response.headers,
      });
    }

    return response;
  };
}

/**
 * Default middleware matcher configuration.
 *
 * Includes root path "/" explicitly to work correctly with Next.js basePath.
 * Excludes Next.js internals and common static files.
 *
 * @example
 * // middleware.ts
 * import { createAuthMiddleware, DEFAULT_MIDDLEWARE_MATCHER } from '@kamiwaza/auth';
 *
 * export const middleware = createAuthMiddleware();
 * export const config = { matcher: DEFAULT_MIDDLEWARE_MATCHER };
 */
export const DEFAULT_MIDDLEWARE_MATCHER = [
  // Root path must be explicit - the regex pattern doesn't match empty paths with basePath
  '/',
  // All other paths except static assets
  '/((?!_next/static|_next/image|favicon.ico|sitemap.xml|robots.txt).+)',
];
