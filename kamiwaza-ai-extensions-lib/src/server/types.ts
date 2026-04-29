/** Server-side identity extracted from platform headers.
 *
 * Mirrors ``kamiwaza_extensions_lib.identity.Identity`` (Python). Fields use
 * camelCase per TS convention; the canonical test vectors use snake_case
 * keys, so test code projects between the two — see
 * ``tests/identity-parity.test.ts``.
 */
export interface Identity {
    userId: string | null;
    email: string | null;
    name: string | null;
    roles: string[];
    /** Platform classification (e.g. "U", "TS"). NOT a boolean. */
    systemHigh: string | null;
    workroomId: string | null;
    workroomRole: string | null;
    requestId: string | null;
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
