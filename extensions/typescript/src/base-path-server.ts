/**
 * Server-side base path utilities for Next.js App Router.
 *
 * These utilities work in Server Components, API routes, and middleware
 * where browser APIs are not available.
 */

import { cookies, headers } from 'next/headers';
import { BASE_PATH_COOKIE, normalizeBasePath, parseCookie } from './base-path';

/**
 * Get the base path on the server side using Next.js headers() and cookies().
 *
 * Checks x-forwarded-prefix header first (set by Traefik), then falls back
 * to the base path cookie.
 *
 * Note: This function is async to support both Next.js 14 and 15.
 * In Next.js 15, headers() and cookies() return Promises.
 *
 * @returns The normalized base path, or empty string if not set
 *
 * @example
 * // In a Server Component
 * import { getBasePathServer } from '@kamiwaza/auth';
 *
 * export default async function Page() {
 *   const basePath = await getBasePathServer();
 *   return <a href={`${basePath}/dashboard`}>Dashboard</a>;
 * }
 */
export const getBasePathServer = async (): Promise<string> => {
  // In Next.js 15, headers() and cookies() return Promises
  // In Next.js 14, they return the value directly
  // We handle both cases by awaiting the result
  const headerStore = await headers();
  const cookieStore = await cookies();

  const headerPrefix = headerStore.get('x-forwarded-prefix');
  const cookiePrefix = cookieStore.get(BASE_PATH_COOKIE)?.value;
  return normalizeBasePath(headerPrefix || cookiePrefix);
};

/**
 * Get the base path from raw request data.
 *
 * Use this in contexts where Next.js headers()/cookies() aren't available,
 * such as middleware or edge functions with raw request objects.
 *
 * @param rawCookies - The raw cookie header string
 * @param forwardedPrefix - The x-forwarded-prefix header value
 * @returns The normalized base path
 *
 * @example
 * // In middleware or edge function
 * import { getRequestBasePath } from '@kamiwaza/auth';
 *
 * const basePath = getRequestBasePath(
 *   request.headers.get('cookie'),
 *   request.headers.get('x-forwarded-prefix')
 * );
 */
export const getRequestBasePath = (
  rawCookies?: string | null,
  forwardedPrefix?: string | null
): string => {
  const fromHeader = forwardedPrefix || undefined;
  const fromCookie = rawCookies ? parseCookie(rawCookies, BASE_PATH_COOKIE) : '';
  return normalizeBasePath(fromHeader || fromCookie);
};
