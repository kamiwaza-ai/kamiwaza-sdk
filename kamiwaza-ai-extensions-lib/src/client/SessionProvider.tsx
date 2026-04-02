"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { SessionCtx } from "./SessionContext";
import type { Session, SessionProviderProps } from "./types";

function normalizeBase(path: string | undefined): string {
    if (!path) return "";
    // Strip trailing slashes, ensure leading slash
    const cleaned = path.replace(/\/+$/, "");
    return cleaned.startsWith("/") ? cleaned : `/${cleaned}`;
}

/**
 * Provides session state to the component tree.
 *
 * Fetches the session from the backend on mount and periodically
 * refreshes it.  The base path is read from
 * `NEXT_PUBLIC_APP_BASE_PATH` (or the `basePath` prop) so that
 * session endpoint calls work in all deployment modes.
 */
export function SessionProvider({
    children,
    basePath,
    sessionEndpoint = "/session",
    refreshInterval = 60_000,
}: SessionProviderProps) {
    const [session, setSession] = useState<Session | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<Error | null>(null);

    const base = useMemo(
        () =>
            normalizeBase(
                basePath ??
                    (typeof window !== "undefined"
                        ? process.env.NEXT_PUBLIC_APP_BASE_PATH
                        : undefined)
            ),
        [basePath]
    );

    const fetchSession = useCallback(async () => {
        try {
            const url = `${base}${sessionEndpoint}`;
            const res = await fetch(url, { credentials: "include" });
            if (!res.ok) {
                throw new Error(`Session fetch failed: ${res.status}`);
            }
            const data = await res.json();
            setSession({
                userId: data.user_id ?? null,
                email: data.email ?? null,
                name: data.name ?? null,
                roles: data.roles ?? [],
                workroomId: data.workroom_id ?? null,
                isAuthenticated: data.is_authenticated ?? false,
                expiresAt: data.expires_at,
            });
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err : new Error(String(err)));
        } finally {
            setLoading(false);
        }
    }, [base, sessionEndpoint]);

    const logout = useCallback(async () => {
        try {
            const url = `${base}/auth/logout`;
            const res = await fetch(url, {
                method: "POST",
                credentials: "include",
            });
            if (res.ok) {
                const data = await res.json();
                if (data.logout_url) {
                    window.location.href = data.logout_url;
                    return;
                }
            }
        } catch {
            // Fall through to reset
        }
        setSession(null);
    }, [base]);

    // Initial fetch
    useEffect(() => {
        fetchSession();
    }, [fetchSession]);

    // Periodic refresh
    useEffect(() => {
        if (refreshInterval <= 0) return;
        const id = setInterval(fetchSession, refreshInterval);
        return () => clearInterval(id);
    }, [fetchSession, refreshInterval]);

    const ctx = useMemo(
        () => ({ session, loading, error, logout, refresh: fetchSession }),
        [session, loading, error, logout, fetchSession]
    );

    return <SessionCtx.Provider value={ctx}>{children}</SessionCtx.Provider>;
}
