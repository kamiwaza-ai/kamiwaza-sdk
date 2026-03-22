/**
 * Middleware exports for Kamiwaza auth.
 *
 * This module exports the middleware function and configuration.
 * Import from '@kamiwaza/auth/middleware' in middleware.ts.
 *
 * @example
 * // In middleware.ts
 * import { createAuthMiddleware, DEFAULT_MIDDLEWARE_MATCHER } from '@kamiwaza/auth/middleware';
 *
 * export const middleware = createAuthMiddleware();
 * export const config = { matcher: DEFAULT_MIDDLEWARE_MATCHER };
 */

export {
  createAuthMiddleware,
  DEFAULT_MIDDLEWARE_MATCHER,
} from './middleware';
export type { MiddlewareConfig } from './middleware';
