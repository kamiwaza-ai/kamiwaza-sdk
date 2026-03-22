'use client';

/**
 * AuthGuard component for protecting routes that require authentication.
 *
 * Wraps child components and handles:
 * - Public route exclusion
 * - Loading state display
 * - Auth error handling with login redirect
 * - Auth-disabled passthrough
 *
 * @example
 * // In app/layout.tsx
 * import { SessionProvider, AuthGuard } from '@kamiwaza/auth';
 *
 * export default function RootLayout({ children }) {
 *   return (
 *     <SessionProvider>
 *       <AuthGuard>
 *         {children}
 *       </AuthGuard>
 *     </SessionProvider>
 *   );
 * }
 *
 * @example
 * // With custom public routes
 * <AuthGuard publicRoutes={['/logged-out', '/about', '/help']}>
 *   {children}
 * </AuthGuard>
 *
 * @example
 * // With custom loading component
 * <AuthGuard
 *   loadingComponent={<MySpinner />}
 *   redirectingComponent={<MyRedirectMessage />}
 * >
 *   {children}
 * </AuthGuard>
 */

import React from 'react';
import { usePathname } from 'next/navigation';
import { useSession } from '../session/context';
import { buildLoginUrl } from '../session/fetch';
import { getBasePathClient } from '../base-path';
import type { AuthGuardConfig } from '../session/types';

interface AuthGuardProps extends AuthGuardConfig {
  children: React.ReactNode;
}

/**
 * Default loading component shown while verifying session.
 */
const DefaultLoadingComponent = (
  <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">
    Verifying session…
  </div>
);

/**
 * Default redirecting component shown while redirecting to login.
 */
const DefaultRedirectingComponent = (
  <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">
    Redirecting to login…
  </div>
);

/**
 * Route protection component that handles authentication flow.
 *
 * Must be used within a SessionProvider context.
 *
 * @param children - Child components to render when authenticated
 * @param publicRoutes - Routes that don't require authentication (default: ['/logged-out'])
 * @param loadingComponent - Custom loading component
 * @param redirectingComponent - Custom redirecting component
 * @param loginUrlEndpoint - Login URL endpoint (default: '/api/auth/login-url')
 * @param fallbackLoginUrl - Fallback login URL if endpoint fails
 */
export function AuthGuard({
  children,
  publicRoutes = ['/logged-out'],
  loadingComponent = DefaultLoadingComponent,
  redirectingComponent = DefaultRedirectingComponent,
  loginUrlEndpoint = '/api/auth/login-url',
  fallbackLoginUrl = '/api/auth/login-url',
}: AuthGuardProps) {
  const pathname = usePathname();
  const { loading, error, session, authEnabled } = useSession();

  // Skip auth check for public routes
  const basePath = getBasePathClient();
  const pathWithoutBase = pathname.startsWith(basePath)
    ? pathname.slice(basePath.length)
    : pathname;

  if (publicRoutes.some((route) => pathWithoutBase.startsWith(route))) {
    return <>{children}</>;
  }

  const handleRedirectToLogin = async () => {
    // Determine the redirect target based on routing mode:
    // - Path-based routing (basePath is set): Use basePath + pathname + search
    // - Port-based routing (basePath is empty): Use full href to preserve port
    let redirectTarget: string;
    if (basePath) {
      // Path-based routing: Combine base path with current path
      redirectTarget =
        basePath + window.location.pathname + window.location.search;
    } else {
      // Port-based routing: Use full URL to preserve the port number
      // This is critical for port-based deployments like https://host:61103/
      redirectTarget = window.location.href;
    }

    const loginUrl = await buildLoginUrl(
      redirectTarget,
      basePath,
      loginUrlEndpoint,
      fallbackLoginUrl
    );
    window.location.assign(loginUrl);
  };

  // Handle any auth error by redirecting to login
  if (error) {
    handleRedirectToLogin();
    return <>{redirectingComponent}</>;
  }

  // Show loading state
  if (loading) {
    return <>{loadingComponent}</>;
  }

  // Auth disabled - allow access
  if (!authEnabled) {
    return <>{children}</>;
  }

  // Auth enabled but no session - redirect to login
  if (!session) {
    handleRedirectToLogin();
    return <>{redirectingComponent}</>;
  }

  // Session valid - render children
  return <>{children}</>;
}
