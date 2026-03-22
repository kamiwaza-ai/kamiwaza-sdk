/**
 * API proxy utilities for forwarding requests to backend with auth headers.
 */

import { NextRequest, NextResponse } from 'next/server';

/**
 * Headers that should not be forwarded between client and backend.
 */
const EXCLUDED_HEADERS = new Set([
  'host',
  'connection',
  'content-length',
  'transfer-encoding',
  'upgrade',
]);

/**
 * Configuration for the API proxy.
 */
export interface ProxyConfig {
  /** Backend URL (default: http://backend:8000) */
  backendUrl?: string;
  /** API path prefix on backend (default: /api) */
  apiPrefix?: string;
  /** Additional headers to exclude from forwarding */
  excludeHeaders?: string[];
}

/**
 * Build the target URL for proxying a request.
 *
 * @param request - The incoming Next.js request
 * @param pathSegments - Path segments from the catch-all route
 * @param config - Proxy configuration
 * @returns The full URL to proxy the request to
 *
 * @example
 * const url = buildTargetUrl(request, ['users', '123'], { backendUrl: 'http://backend:8000' });
 * // Returns: http://backend:8000/api/users/123
 */
export const buildTargetUrl = (
  request: NextRequest,
  pathSegments: string[],
  config: ProxyConfig = {}
): string => {
  const backendUrl = (config.backendUrl || process.env.BACKEND_URL || 'http://backend:8000').replace(/\/$/, '');
  const apiPrefix = config.apiPrefix ?? '/api';
  const apiBase = backendUrl.endsWith(apiPrefix) ? backendUrl : `${backendUrl}${apiPrefix}`;

  const joined = pathSegments.join('/');
  const path = joined ? `/${joined}` : '';
  const search = request.nextUrl.search || '';
  return `${apiBase}${path}${search}`;
};

/**
 * Extract headers to forward from the incoming request.
 *
 * Filters out hop-by-hop headers and preserves authentication headers
 * (cookies, authorization) and other relevant headers.
 *
 * @param request - The incoming Next.js request
 * @param config - Proxy configuration
 * @returns Headers object to use for the proxied request
 *
 * @example
 * const headers = forwardHeaders(request);
 * const response = await fetch(url, { headers });
 */
export const forwardHeaders = (
  request: NextRequest,
  config: ProxyConfig = {}
): HeadersInit => {
  const backendUrl = config.backendUrl || process.env.BACKEND_URL || 'http://backend:8000';
  const excludeSet = new Set([
    ...EXCLUDED_HEADERS,
    ...(config.excludeHeaders || []).map((h) => h.toLowerCase()),
  ]);

  const headers: Record<string, string> = {};
  request.headers.forEach((value, key) => {
    if (!excludeSet.has(key.toLowerCase())) {
      headers[key] = value;
    }
  });

  // Set host to backend host
  headers.host = new URL(backendUrl).host;

  // Preserve x-forwarded-prefix for App Garden path routing
  const forwardedPrefix = request.headers.get('x-forwarded-prefix');
  if (forwardedPrefix) {
    headers['x-forwarded-prefix'] = forwardedPrefix;
  }

  return headers;
};

/**
 * Forward a request to the backend.
 *
 * @param request - The incoming Next.js request
 * @param pathSegments - Path segments from the catch-all route
 * @param config - Proxy configuration
 * @returns The proxied response
 */
const forward = async (
  request: NextRequest,
  pathSegments: string[],
  config: ProxyConfig = {}
): Promise<NextResponse> => {
  const url = buildTargetUrl(request, pathSegments, config);
  const init: RequestInit = {
    method: request.method,
    headers: forwardHeaders(request, config),
    cache: 'no-store',
  };

  // Handle request body for non-GET/HEAD methods
  const methodAllowsBody = !['GET', 'HEAD'].includes(request.method);
  if (methodAllowsBody && request.body) {
    const contentType = request.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      const json = await request.json();
      init.body = JSON.stringify(json);
    } else {
      init.body = request.body as ReadableStream<Uint8Array>;
      // @ts-expect-error duplex is required by Next.js/Edge runtime when streaming body
      init.duplex = 'half';
    }
  }

  const response = await fetch(url, init);

  // Filter response headers
  const filtered = new Headers();
  response.headers.forEach((value, key) => {
    if (!EXCLUDED_HEADERS.has(key.toLowerCase())) {
      filtered.set(key, value);
    }
  });

  // Handle 204 No Content
  if (response.status === 204) {
    return new NextResponse(null, { status: 204, headers: filtered });
  }

  // Handle JSON responses
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    const data = await response.json();
    return NextResponse.json(data, { status: response.status, headers: filtered });
  }

  // Handle other responses
  const buffer = await response.arrayBuffer();
  return new NextResponse(buffer, { status: response.status, headers: filtered });
};

type RouteContext = { params: Promise<{ path?: string[] }> | { path?: string[] } };

/**
 * Create a handler for a specific HTTP method.
 */
const buildHandler = (method: string, config: ProxyConfig = {}) => {
  return async (request: NextRequest, context: RouteContext): Promise<NextResponse> => {
    if (request.method !== method) {
      return NextResponse.json({ error: 'Method Not Allowed' }, { status: 405 });
    }
    try {
      // Handle both Next.js 14 (sync) and Next.js 15 (async) params
      const params = 'then' in context.params ? await context.params : context.params;
      return await forward(request, params.path || [], config);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unexpected proxy error';
      return NextResponse.json({ error: 'Proxy error', message }, { status: 502 });
    }
  };
};

/**
 * Create all HTTP method handlers for API proxy routes.
 *
 * @param config - Proxy configuration
 * @returns Object with GET, POST, PUT, PATCH, DELETE handlers
 *
 * @example
 * // In app/api/[...path]/route.ts
 * import { createProxyHandlers } from '@kamiwaza/auth';
 *
 * const { GET, POST, PUT, PATCH, DELETE } = createProxyHandlers({
 *   backendUrl: process.env.BACKEND_URL,
 * });
 *
 * export { GET, POST, PUT, PATCH, DELETE };
 */
export const createProxyHandlers = (config: ProxyConfig = {}) => ({
  GET: buildHandler('GET', config),
  POST: buildHandler('POST', config),
  PUT: buildHandler('PUT', config),
  PATCH: buildHandler('PATCH', config),
  DELETE: buildHandler('DELETE', config),
});

// Default handlers for simple usage
const defaultHandlers = createProxyHandlers();
export const GET = defaultHandlers.GET;
export const POST = defaultHandlers.POST;
export const PUT = defaultHandlers.PUT;
export const PATCH = defaultHandlers.PATCH;
export const DELETE = defaultHandlers.DELETE;
