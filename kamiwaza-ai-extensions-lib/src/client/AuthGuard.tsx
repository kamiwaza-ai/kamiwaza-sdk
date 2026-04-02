"use client";

import React, { useEffect, useState } from "react";
import { useSession } from "./useSession";
import type { AuthGuardProps } from "./types";

/**
 * Protects children behind authentication.
 *
 * - While loading: renders `fallback` (default: nothing).
 * - If authenticated: renders children.
 * - If not authenticated and auth is enabled: redirects to login.
 * - If not authenticated and no login URL (local dev): renders children.
 */
export function AuthGuard({ children, fallback = null }: AuthGuardProps) {
    const { session, loading } = useSession();
    const [redirecting, setRedirecting] = useState(false);

    useEffect(() => {
        if (loading || redirecting) return;
        if (session?.isAuthenticated) return;

        // Session loaded but user is not authenticated — try login redirect.
        const basePath =
            process.env.NEXT_PUBLIC_APP_BASE_PATH ?? "";
        const loginUrlEndpoint = `${basePath}/auth/login-url`;

        fetch(loginUrlEndpoint, { credentials: "include" })
            .then((res) => res.json())
            .then((data) => {
                if (data.login_url) {
                    setRedirecting(true);
                    window.location.href = data.login_url;
                }
                // If login_url is null (local dev), do nothing — render children
            })
            .catch(() => {
                // Can't get login URL — render children (best-effort)
            });
    }, [session, loading, redirecting]);

    if (loading || redirecting) {
        return <>{fallback}</>;
    }

    return <>{children}</>;
}
