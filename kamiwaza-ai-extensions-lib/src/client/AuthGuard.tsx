"use client";

import React, { useEffect, useRef, useState } from "react";
import { useSession } from "./useSession";
import type { AuthGuardProps } from "./types";

/**
 * Protects children behind authentication.
 *
 * - While loading or resolving auth: renders `fallback` (default: nothing).
 * - If authenticated: renders children.
 * - If not authenticated and auth is enabled: redirects to login.
 * - If not authenticated and no login URL (local dev): renders children.
 *
 * Children are NEVER rendered to unauthenticated users in production
 * — the component stays in the fallback state until auth is fully resolved.
 */
export function AuthGuard({ children, fallback = null }: AuthGuardProps) {
    const { session, loading, basePath } = useSession();
    const [resolved, setResolved] = useState(false);
    const abortRef = useRef<AbortController | null>(null);

    useEffect(() => {
        if (loading) return;
        if (session?.isAuthenticated) {
            setResolved(true);
            return;
        }

        // Session loaded but user is not authenticated — fetch login URL.
        const controller = new AbortController();
        abortRef.current = controller;

        const loginUrlEndpoint = `${basePath}/auth/login-url`;

        fetch(loginUrlEndpoint, {
            credentials: "include",
            signal: controller.signal,
        })
            .then((res) => res.json())
            .then((data) => {
                if (controller.signal.aborted) return;
                if (data.login_url) {
                    // Redirect — keep showing fallback (resolved stays false)
                    window.location.href = data.login_url;
                } else {
                    // No login URL (local dev) — allow rendering children
                    setResolved(true);
                }
            })
            .catch((err) => {
                if (controller.signal.aborted) return;
                // Can't determine auth state — allow rendering as best-effort
                console.warn("AuthGuard: failed to fetch login URL", err);
                setResolved(true);
            });

        return () => {
            controller.abort();
        };
    }, [session, loading, basePath]);

    // Show fallback until we know the user is authed or in local dev mode
    if (loading || !resolved) {
        return <>{fallback}</>;
    }

    return <>{children}</>;
}
