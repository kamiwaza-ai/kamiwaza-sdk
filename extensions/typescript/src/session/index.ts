/**
 * Session management for Kamiwaza auth.
 *
 * Provides React context, hooks, and utilities for managing user sessions
 * in App Garden applications.
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
 * // In any component
 * import { useSession, isAuthenticated } from '@kamiwaza/auth';
 *
 * function Header() {
 *   const { session, logout } = useSession();
 *
 *   if (!isAuthenticated(session)) {
 *     return <LoginButton />;
 *   }
 *
 *   return (
 *     <div>
 *       <span>{session.email}</span>
 *       <button onClick={() => logout()}>Logout</button>
 *     </div>
 *   );
 * }
 */

// Types
export type {
  SessionData,
  LogoutResponse,
  SessionExpiredError,
  SessionContextValue,
  SessionProviderConfig,
  AuthGuardConfig,
} from './types';

// Context and hooks
export { SessionProvider, useSession } from './context';

// Fetch utilities
export {
  fetchSession,
  logout,
  calculateTimeRemaining,
  isAuthenticated,
  buildLoginUrl,
} from './fetch';
