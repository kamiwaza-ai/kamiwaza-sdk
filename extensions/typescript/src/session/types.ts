/**
 * Session management types for Kamiwaza auth.
 *
 * These types define the session data structures and context interfaces
 * used across the authentication system.
 */

/**
 * Session data returned from /api/session endpoint.
 * When auth_enabled=false, session_expires_at is omitted.
 */
export interface SessionData {
  user_id: string;
  email: string;
  name: string;
  roles: string[];
  request_id?: string | null;
  auth_enabled: boolean;
  /** Unix timestamp, only present when auth_enabled=true */
  session_expires_at?: number;
}

/**
 * Logout response from /api/auth/logout endpoint.
 */
export interface LogoutResponse {
  success: boolean;
  message: string;
  redirect_url?: string;
  front_channel_logout_url?: string;
}

/**
 * Session expired error details.
 */
export interface SessionExpiredError {
  error: 'session_expired';
  message: string;
}

/**
 * Context value exposed to consumers via useSession hook.
 */
export interface SessionContextValue {
  /** Current session data, null if not loaded or error */
  session: SessionData | null;
  /** True while session is being fetched */
  loading: boolean;
  /** Error from session fetch, null if successful */
  error: Error | null;
  /** Whether authentication is enabled on the backend */
  authEnabled: boolean;
  /** Seconds until session expires, undefined if auth disabled */
  secondsRemaining: number | undefined;
  /** Logout and clear session */
  logout: (redirectUri?: string) => Promise<LogoutResponse>;
  /** Refresh session data from backend */
  refreshSession: () => Promise<void>;
}

/**
 * Configuration options for SessionProvider.
 */
export interface SessionProviderConfig {
  /** Base path for API calls (default: reads from NEXT_PUBLIC_APP_BASE_PATH) */
  basePath?: string;
  /** Session endpoint path (default: '/api/session') */
  sessionEndpoint?: string;
  /** Logout endpoint path (default: '/api/auth/logout') */
  logoutEndpoint?: string;
}

/**
 * Configuration options for AuthGuard component.
 */
export interface AuthGuardConfig {
  /** Routes that don't require authentication (default: ['/logged-out']) */
  publicRoutes?: string[];
  /** Custom loading component */
  loadingComponent?: React.ReactNode;
  /** Custom redirecting component */
  redirectingComponent?: React.ReactNode;
  /** Login URL endpoint (default: '/api/auth/login-url') */
  loginUrlEndpoint?: string;
  /** Fallback login URL if endpoint fails */
  fallbackLoginUrl?: string;
}
