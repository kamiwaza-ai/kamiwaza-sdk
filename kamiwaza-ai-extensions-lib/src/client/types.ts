/** Session state returned by the backend /session endpoint. */
export interface Session {
    userId: string | null;
    email: string | null;
    name: string | null;
    roles: string[];
    workroomId: string | null;
    isAuthenticated: boolean;
    expiresAt?: number;
}

/** Context value exposed by SessionProvider. */
export interface SessionContext {
    session: Session | null;
    loading: boolean;
    error: Error | null;
    logout: () => Promise<void>;
    refresh: () => Promise<void>;
}

/** Props for SessionProvider. */
export interface SessionProviderProps {
    children: React.ReactNode;
    /** Override for NEXT_PUBLIC_APP_BASE_PATH. */
    basePath?: string;
    /** Paths that skip auth checks (e.g., ["/logged-out"]). */
    publicRoutes?: string[];
    /** Session endpoint path (default: "/session"). */
    sessionEndpoint?: string;
    /** Refresh interval in ms (default: 60000). */
    refreshInterval?: number;
}

/** Props for AuthGuard. */
export interface AuthGuardProps {
    children: React.ReactNode;
    /** Shown while session is loading. */
    fallback?: React.ReactNode;
}

/** Model metadata returned by the backend. */
export interface AvailableModel {
    id: string;
    name: string;
    repoId?: string;
    type?: string;
    capabilities?: string[];
    status: string;
}
