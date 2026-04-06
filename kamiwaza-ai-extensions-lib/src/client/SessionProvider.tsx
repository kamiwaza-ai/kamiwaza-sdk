"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { SessionCtx } from "./SessionContext";
import type { Session, SessionProviderProps } from "./types";

function normalizeBase(path: string | undefined): string {
    if (!path) return "";
    // Strip trailing slashes, ensure leading slash
    const cleaned = path.replace(/\/+$/, "");
    return cleaned.startsWith("/") ? cleaned : `/${cleaned}`;
}

/** Maximum backoff interval on repeated failures (5 minutes). */
const MAX_BACKOFF_MS = 5 * 60_000;

/**
 * Provides session state to the component tree.
 *
 * Fetches the session from the backend on mount and periodically
 * refreshes it.  The base path is read from
 * `NEXT_PUBLIC_APP_BASE_PATH` (or the `basePath` prop) so that
 * session endpoint calls work in all deployment modes.
 *
 * On repeated fetch failures the refresh interval backs off
 * exponentially (capped at 5 minutes) and resets on success.
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

    // Track consecutive failures for backoff
    const failCountRef = useRef(0);
    const backoffRef = useRef(refreshInterval);

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

    const fetchSession = useCallback(async (signal?: AbortSignal) => {
        try {
            const url = `${base}${sessionEndpoint}`;
            const res = await fetch(url, { credentials: "include", signal });
            if (!res.ok) {
                throw new Error(`Session fetch failed: ${res.status}`);
            }
            const data = await res.json();
            if (signal?.aborted) return;
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
            // Reset backoff on success
            failCountRef.current = 0;
            backoffRef.current = refreshInterval;
        } catch (err) {
            if (signal?.aborted) return;
            setError(err instanceof Error ? err : new Error(String(err)));
            // Increase backoff on failure
            failCountRef.current += 1;
            backoffRef.current = Math.min(
                refreshInterval * 2 ** failCountRef.current,
                MAX_BACKOFF_MS
            );
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [base, sessionEndpoint, refreshInterval]);

    const logout = useCallback(async () => {
        try {
            const url = `${base}/auth/logout`;
            const res = await fetch(url, {
                method: "POST",
                credentials: "include",
            });
            if (res.ok) {
                const data = await res.json();
                // Prefer redirect_url (app's logged-out page) over logout_url
                // (platform endpoint that requires POST and can't be GET-navigated)
                const target = data.redirect_url || data.logout_url;
                if (target) {
                    window.location.href = target;
                    return;
                }
            }
        } catch {
            // Fall through to reset
        }
        setSession(null);
    }, [base]);

    // Initial fetch with abort on unmount
    useEffect(() => {
        const controller = new AbortController();
        fetchSession(controller.signal);
        return () => controller.abort();
    }, [fetchSession]);

    // Periodic refresh with backoff and abort on cleanup
    useEffect(() => {
        if (refreshInterval <= 0) return;
        let cancelled = false;
        let timerId: ReturnType<typeof setTimeout>;
        let controller = new AbortController();

        const tick = () => {
            controller = new AbortController();
            fetchSession(controller.signal).finally(() => {
                if (!cancelled) {
                    timerId = setTimeout(tick, backoffRef.current);
                }
            });
        };
        timerId = setTimeout(tick, backoffRef.current);

        return () => {
            cancelled = true;
            clearTimeout(timerId);
            controller.abort();
        };
    }, [fetchSession, refreshInterval]);

    const ctx = useMemo(
        () => ({ session, loading, error, basePath: base, logout, refresh: fetchSession }),
        [session, loading, error, base, logout, fetchSession]
    );

    return <SessionCtx.Provider value={ctx}>{children}</SessionCtx.Provider>;
}
