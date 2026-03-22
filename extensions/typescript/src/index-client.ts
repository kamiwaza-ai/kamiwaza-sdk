/**
 * Client-side exports for Kamiwaza auth.
 *
 * This module exports React components and hooks that require 'use client'.
 * Import from '@kamiwaza/auth/client' in client components.
 *
 * @example
 * // In app/layout.tsx
 * import { SessionProvider, AuthGuard, useSession } from '@kamiwaza/auth/client';
 */

// Client-side base path utilities
export {
  withBase,
  getBasePath,
  getBasePathClient,
  BASE_PATH_COOKIE,
  parseCookie,
  normalizeBasePath,
} from './base-path';

// Fetch utilities (client-side)
export { apiFetch, createApiFetch } from './fetch';

// Session management (React components and hooks)
export {
  SessionProvider,
  useSession,
  fetchSession,
  logout,
  calculateTimeRemaining,
  isAuthenticated,
  buildLoginUrl,
} from './session';
export type {
  SessionData,
  LogoutResponse,
  SessionExpiredError,
  SessionContextValue,
  SessionProviderConfig,
  AuthGuardConfig,
} from './session';

// Components
export { AuthGuard } from './components';
