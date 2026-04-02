/** Server-side identity extracted from platform headers. */
export interface Identity {
    userId: string | null;
    email: string | null;
    name: string | null;
    roles: string[];
    workroomId: string | null;
    isAuthenticated: boolean;
}

/** Configuration for createProxyHandlers. */
export interface ProxyConfig {
    /** Backend URL, e.g., "http://backend:8000". */
    target: string;
    /** Strip this prefix from the request path before forwarding. */
    pathPrefix?: string;
}

/** Model metadata from the backend API. */
export interface AvailableModel {
    id: string;
    name: string;
    repoId?: string;
    type?: string;
    capabilities?: string[];
    status: string;
}
