import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import { render, screen, waitFor, act } from "@testing-library/react";
import { SessionCtx } from "../src/client/SessionContext";
import { AuthGuard } from "../src/client/AuthGuard";
import type { SessionContext } from "../src/client/types";

function renderWithSession(ctx: Partial<SessionContext>, ui: React.ReactElement) {
    const full: SessionContext = {
        session: null,
        loading: true,
        error: null,
        basePath: "",
        logout: async () => {},
        refresh: async () => {},
        ...ctx,
    };
    return render(
        <SessionCtx.Provider value={full}>{ui}</SessionCtx.Provider>
    );
}

describe("AuthGuard", () => {
    let fetchSpy: ReturnType<typeof vi.fn>;

    beforeEach(() => {
        fetchSpy = vi.fn();
        vi.stubGlobal("fetch", fetchSpy);
    });

    afterEach(() => {
        vi.unstubAllGlobals();
    });

    it("shows fallback while loading", () => {
        renderWithSession(
            { loading: true },
            <AuthGuard fallback={<div>Loading...</div>}>
                <div>Protected</div>
            </AuthGuard>
        );

        expect(screen.getByText("Loading...")).toBeDefined();
        expect(screen.queryByText("Protected")).toBeNull();
    });

    it("shows children when authenticated", async () => {
        renderWithSession(
            {
                loading: false,
                session: {
                    userId: "usr-123",
                    email: "a@b.com",
                    name: "Alice",
                    roles: [],
                    workroomId: null,
                    isAuthenticated: true,
                },
            },
            <AuthGuard fallback={<div>Loading...</div>}>
                <div>Protected</div>
            </AuthGuard>
        );

        await waitFor(() => {
            expect(screen.getByText("Protected")).toBeDefined();
        });
        expect(screen.queryByText("Loading...")).toBeNull();
    });

    it("does NOT show children before auth is resolved (unauthenticated)", async () => {
        // Login URL fetch is pending — children must not flash
        fetchSpy.mockReturnValue(new Promise(() => {})); // never resolves

        renderWithSession(
            {
                loading: false,
                session: {
                    userId: null,
                    email: null,
                    name: null,
                    roles: [],
                    workroomId: null,
                    isAuthenticated: false,
                },
            },
            <AuthGuard fallback={<div>Loading...</div>}>
                <div>Protected</div>
            </AuthGuard>
        );

        // Fallback should be showing, NOT protected content
        expect(screen.getByText("Loading...")).toBeDefined();
        expect(screen.queryByText("Protected")).toBeNull();
    });

    it("shows children in local dev mode (login_url is null)", async () => {
        fetchSpy.mockResolvedValue({
            ok: true,
            json: async () => ({ login_url: null }),
        });

        renderWithSession(
            {
                loading: false,
                session: {
                    userId: null,
                    email: null,
                    name: null,
                    roles: [],
                    workroomId: null,
                    isAuthenticated: false,
                },
            },
            <AuthGuard fallback={<div>Loading...</div>}>
                <div>Protected</div>
            </AuthGuard>
        );

        await waitFor(() => {
            expect(screen.getByText("Protected")).toBeDefined();
        });
    });

    it("stays on fallback when redirect login_url is returned", async () => {
        // Simulate a login_url response — AuthGuard will try to redirect
        // via window.location.href. In jsdom this is a no-op, but the key
        // assertion is that children are never rendered.
        let resolveLogin: (v: unknown) => void;
        const loginPromise = new Promise((r) => { resolveLogin = r; });

        fetchSpy.mockReturnValue(loginPromise);

        renderWithSession(
            {
                loading: false,
                session: {
                    userId: null,
                    email: null,
                    name: null,
                    roles: [],
                    workroomId: null,
                    isAuthenticated: false,
                },
            },
            <AuthGuard fallback={<div>Loading...</div>}>
                <div>Protected</div>
            </AuthGuard>
        );

        // While login URL fetch is pending: fallback shown, NOT children
        expect(screen.getByText("Loading...")).toBeDefined();
        expect(screen.queryByText("Protected")).toBeNull();

        // Resolve with a login URL
        await act(async () => {
            resolveLogin!({
                ok: true,
                json: async () => ({ login_url: "https://cluster.test/auth/login" }),
            });
        });

        // After redirect attempt: still fallback (resolved never set to true)
        expect(screen.queryByText("Protected")).toBeNull();
    });

    it("fails CLOSED on login-url fetch error (does NOT render children)", async () => {
        fetchSpy.mockRejectedValue(new Error("Network error"));

        renderWithSession(
            {
                loading: false,
                session: {
                    userId: null,
                    email: null,
                    name: null,
                    roles: [],
                    workroomId: null,
                    isAuthenticated: false,
                },
            },
            <AuthGuard fallback={<div>Loading...</div>}>
                <div>Protected</div>
            </AuthGuard>
        );

        // Wait for the fetch to reject
        await waitFor(() => {
            expect(fetchSpy).toHaveBeenCalled();
        });

        // Must show fallback, NOT children — fail closed
        expect(screen.getByText("Loading...")).toBeDefined();
        expect(screen.queryByText("Protected")).toBeNull();
    });

    it("uses basePath from session context for login URL fetch", async () => {
        fetchSpy.mockResolvedValue({
            ok: true,
            json: async () => ({ login_url: null }),
        });

        renderWithSession(
            {
                loading: false,
                basePath: "/runtime/apps/my-app",
                session: {
                    userId: null,
                    email: null,
                    name: null,
                    roles: [],
                    workroomId: null,
                    isAuthenticated: false,
                },
            },
            <AuthGuard><div>Content</div></AuthGuard>
        );

        await waitFor(() => {
            expect(fetchSpy).toHaveBeenCalledWith(
                "/runtime/apps/my-app/auth/login-url",
                expect.objectContaining({ credentials: "include" })
            );
        });
    });
});
