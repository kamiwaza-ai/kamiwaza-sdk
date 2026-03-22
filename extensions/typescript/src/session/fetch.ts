/**
 * Session fetch utilities for Kamiwaza auth.
 *
 * These functions handle fetching session data and logout operations.
 */

import type { SessionData, LogoutResponse, SessionExpiredError } from './types';
import { getBasePathClient } from '../base-path';

/**
 * Fetch session data from the backend.
 * Throws on auth errors or network failures.
 *
 * @param basePath - Optional base path override (default: auto-detected from cookie)
 * @param endpoint - Session endpoint path (default: '/api/session')
 * @returns Session data from backend
 * @throws Error with message 'session_expired' if session is invalid
 *
 * @example
 * try {
 *   const session = await fetchSession();
 *   console.log('Logged in as:', session.email);
 * } catch (err) {
 *   if (err.message === 'session_expired') {
 *     // Redirect to login
 *   }
 * }
 */
export async function fetchSession(
  basePath?: string,
  endpoint: string = '/api/session'
): Promise<SessionData> {
  const base = basePath ?? getBasePathClient();
  const response = await fetch(`${base}${endpoint}`, {
    credentials: 'include',
  });

  if (!response.ok) {
    const data = await response.json();
    if (response.status === 401 && data?.detail?.error === 'session_expired') {
      const error = new Error('session_expired');
      (error as Error & { detail: SessionExpiredError }).detail = data.detail;
      throw error;
    }
    throw new Error(data?.detail?.message || `HTTP ${response.status}`);
  }

  return response.json();
}

/**
 * Calculate seconds remaining until session expires.
 * Returns undefined if session_expires_at is not set (auth disabled).
 * Returns 0 if session has already expired.
 *
 * @param sessionExpiresAt - Unix timestamp of session expiry
 * @returns Seconds remaining, or undefined if no expiry set
 *
 * @example
 * const remaining = calculateTimeRemaining(session.session_expires_at);
 * if (remaining !== undefined && remaining < 300) {
 *   console.log('Session expiring soon!');
 * }
 */
export function calculateTimeRemaining(
  sessionExpiresAt: number | undefined
): number | undefined {
  if (sessionExpiresAt === undefined) {
    return undefined;
  }

  const now = Math.floor(Date.now() / 1000);
  const remaining = sessionExpiresAt - now;
  return remaining > 0 ? remaining : 0;
}

/**
 * Call logout endpoint and return response with redirect URLs.
 *
 * @param redirectUri - Optional post-logout redirect URI
 * @param basePath - Optional base path override (default: auto-detected from cookie)
 * @param endpoint - Logout endpoint path (default: '/api/auth/logout')
 * @returns Logout response with redirect URLs
 *
 * @example
 * const response = await logout('/');
 * if (response.front_channel_logout_url) {
 *   // Handle front-channel logout
 *   window.location.assign(response.front_channel_logout_url);
 * } else if (response.redirect_url) {
 *   window.location.assign(response.redirect_url);
 * }
 */
export async function logout(
  redirectUri?: string,
  basePath?: string,
  endpoint: string = '/api/auth/logout'
): Promise<LogoutResponse> {
  const base = basePath ?? getBasePathClient();
  const body: { post_logout_redirect_uri?: string } = {};
  if (redirectUri) {
    body.post_logout_redirect_uri = redirectUri;
  }

  const response = await fetch(`${base}${endpoint}`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    // Try to parse error, but handle non-JSON responses
    let errorMessage = `Logout failed: HTTP ${response.status}`;
    try {
      const data = await response.json();
      errorMessage = data?.detail?.message || data?.message || errorMessage;
    } catch {
      // Response wasn't JSON
    }
    console.error('Logout error:', errorMessage);
    // Still return a response so the UI can redirect
    return {
      success: false,
      message: errorMessage,
      redirect_url: undefined,
      front_channel_logout_url: undefined,
    };
  }

  return response.json();
}

/**
 * Check if session represents an authenticated user (not anonymous).
 *
 * @param session - Session data to check
 * @returns True if user is authenticated
 *
 * @example
 * if (isAuthenticated(session)) {
 *   showUserMenu();
 * } else {
 *   showLoginButton();
 * }
 */
export function isAuthenticated(session: SessionData | null): boolean {
  if (!session) return false;
  return session.auth_enabled && session.user_id !== 'anonymous';
}

/**
 * Build login URL by fetching from backend endpoint.
 *
 * @param redirectUri - Where to redirect after login
 * @param basePath - Optional base path override (default: auto-detected from cookie)
 * @param endpoint - Login URL endpoint (default: '/api/auth/login-url')
 * @param fallbackUrl - Fallback URL if fetch fails
 * @returns Login URL to redirect to
 *
 * @example
 * const loginUrl = await buildLoginUrl(window.location.href);
 * window.location.assign(loginUrl);
 */
export async function buildLoginUrl(
  redirectUri?: string,
  basePath?: string,
  endpoint: string = '/api/auth/login-url',
  fallbackUrl: string = '/api/auth/login-url'
): Promise<string> {
  const base = basePath ?? getBasePathClient();
  const redirectTarget = redirectUri || base || window.location.href;

  try {
    const loginUrlPath = `${base}${endpoint}?redirect_uri=${encodeURIComponent(redirectTarget)}`;
    const resp = await fetch(loginUrlPath);
    const data = await resp.json().catch(() => ({}));
    if (typeof data?.login_url === 'string') {
      return data.login_url;
    }
  } catch (err) {
    console.warn('login-url fetch failed', err);
  }

  // Fallback URL must also include base path
  return `${base}${fallbackUrl}`;
}
