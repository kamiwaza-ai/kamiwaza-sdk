"use client";

import React, { useEffect, useState } from "react";
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
 * Fails **closed**: on any error determining auth state, the component
 * stays on fallback rather than rendering protected content.
 */
export function AuthGuard({ children, fallback = null }: AuthGuardProps) {
    const { session, loading, basePath } = useSession();
    const [resolved, setResolved] = useState(false);

    useEffect(() => {
        if (loading) return;
        if (session?.isAuthenticated) {
            setResolved(true);
            return;
        }

        // Session loaded but user is not authenticated — fetch login URL.
        const controller = new AbortController();
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
                // Fail CLOSED: keep showing fallback. Do not render children
                // when we can't determine auth state — this prevents an auth
                // bypass via transient network errors or targeted DoS.
                console.error("AuthGuard: failed to fetch login URL — staying on fallback", err);
            });

        return () => {
            controller.abort();
        };
    }, [session, loading, basePath]);

    if (loading || !resolved) {
        return <>{fallback}</>;
    }

    return <>{children}</>;
}
