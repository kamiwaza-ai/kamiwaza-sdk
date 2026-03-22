'use client';

/**
 * React context and provider for session management.
 *
 * Provides session state and methods to all child components.
 *
 * @example
 * // In app/layout.tsx
 * import { SessionProvider } from '@kamiwaza/auth';
 *
 * export default function RootLayout({ children }) {
 *   return (
 *     <SessionProvider>
 *       {children}
 *     </SessionProvider>
 *   );
 * }
 *
 * @example
 * // In any component
 * import { useSession } from '@kamiwaza/auth';
 *
 * function UserMenu() {
 *   const { session, loading, logout } = useSession();
 *
 *   if (loading) return <Spinner />;
 *   if (!session) return <LoginButton />;
 *
 *   return (
 *     <div>
 *       <span>{session.email}</span>
 *       <button onClick={() => logout()}>Logout</button>
 *     </div>
 *   );
 * }
 */

import React, {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  type ReactNode,
} from 'react';
import type {
  SessionData,
  SessionContextValue,
  SessionProviderConfig,
  LogoutResponse,
} from './types';
import {
  fetchSession as fetchSessionImpl,
  logout as logoutImpl,
  calculateTimeRemaining,
} from './fetch';
import { getBasePathClient } from '../base-path';

const SessionContext = createContext<SessionContextValue | null>(null);

interface SessionProviderProps extends SessionProviderConfig {
  children: ReactNode;
}

/**
 * Provider component that fetches session on mount and provides session state.
 *
 * Wrap your application with this provider to enable session management.
 *
 * @param children - Child components
 * @param basePath - Optional base path override
 * @param sessionEndpoint - Session endpoint path (default: '/api/session')
 * @param logoutEndpoint - Logout endpoint path (default: '/api/auth/logout')
 *
 * @example
 * // Basic usage
 * <SessionProvider>
 *   <App />
 * </SessionProvider>
 *
 * @example
 * // With custom endpoints
 * <SessionProvider
 *   sessionEndpoint="/api/v2/session"
 *   logoutEndpoint="/api/v2/auth/logout"
 * >
 *   <App />
 * </SessionProvider>
 */
export function SessionProvider({
  children,
  basePath,
  sessionEndpoint = '/api/session',
  logoutEndpoint = '/api/auth/logout',
}: SessionProviderProps) {
  const [session, setSession] = useState<SessionData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [secondsRemaining, setSecondsRemaining] = useState<number | undefined>(
    undefined
  );

  const resolvedBasePath = basePath ?? getBasePathClient();

  const refreshSession = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await fetchSessionImpl(resolvedBasePath, sessionEndpoint);
      setSession(data);
      setSecondsRemaining(calculateTimeRemaining(data.session_expires_at));
    } catch (err) {
      setError(err instanceof Error ? err : new Error(String(err)));
      setSession(null);
    } finally {
      setLoading(false);
    }
  }, [resolvedBasePath, sessionEndpoint]);

  // Fetch session on mount
  useEffect(() => {
    refreshSession();
  }, [refreshSession]);

  // Update countdown timer every second when auth is enabled
  useEffect(() => {
    if (!session?.auth_enabled || !session?.session_expires_at) {
      return;
    }

    const interval = setInterval(() => {
      const remaining = calculateTimeRemaining(session.session_expires_at);
      setSecondsRemaining(remaining);

      // Stop interval when session expires
      if (remaining === 0) {
        clearInterval(interval);
      }
    }, 1000);

    return () => clearInterval(interval);
  }, [session?.auth_enabled, session?.session_expires_at]);

  const handleLogout = useCallback(
    async (redirectUri?: string): Promise<LogoutResponse> => {
      const response = await logoutImpl(
        redirectUri,
        resolvedBasePath,
        logoutEndpoint
      );
      setSession(null);
      setSecondsRemaining(undefined);
      return response;
    },
    [resolvedBasePath, logoutEndpoint]
  );

  const value: SessionContextValue = {
    session,
    loading,
    error,
    authEnabled: session?.auth_enabled ?? false,
    secondsRemaining,
    logout: handleLogout,
    refreshSession,
  };

  return (
    <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
  );
}

/**
 * Hook to access session context. Must be used within SessionProvider.
 *
 * @returns Session context value with session data and methods
 * @throws Error if used outside of SessionProvider
 *
 * @example
 * function MyComponent() {
 *   const { session, loading, error, logout, refreshSession } = useSession();
 *
 *   if (loading) return <div>Loading...</div>;
 *   if (error) return <div>Error: {error.message}</div>;
 *   if (!session) return <div>Not logged in</div>;
 *
 *   return (
 *     <div>
 *       <p>Welcome, {session.name}</p>
 *       <button onClick={() => logout()}>Logout</button>
 *     </div>
 *   );
 * }
 */
export function useSession(): SessionContextValue {
  const context = useContext(SessionContext);
  if (!context) {
    throw new Error('useSession must be used within a SessionProvider');
  }
  return context;
}
