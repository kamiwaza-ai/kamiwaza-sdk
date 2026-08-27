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
    /**
     * Strip this prefix from the request path before forwarding.
     * @deprecated Explicit override; the runtime app path is stripped
     * automatically (see {@link ProxyConfig.stripRuntimeAppPath}).
     */
    pathPrefix?: string;
    /**
     * Remove at most one leading occurrence of the deployment's runtime app
     * path (`KAMIWAZA_APP_PATH`) from the request path before forwarding,
     * segment-boundary aware. Default true.
     */
    stripRuntimeAppPath?: boolean;
    /**
     * Upstream paths (after prefix stripping, exact match) for which
     * `Set-Cookie` response headers pass through to the client. Everywhere
     * else `Set-Cookie` is dropped. Defaults to an empty list; cookie
     * passthrough must be explicitly enabled on trusted session routes. In
     * path mode, an allowlisted `__Host-` cookie fails the proxy response with
     * 502 because that cookie cannot be safely scoped below `/`. Opt-in trusts
     * the upstream's cookie names and security attributes: the proxy strips
     * Domain and rebases Path, but does not add HttpOnly, Secure, or SameSite.
     */
    setCookiePaths?: readonly string[];
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
