import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React, { useEffect } from "react";
import { render, waitFor, act } from "@testing-library/react";
import {
    isTrustedFrontChannelUrl,
    resolveLogoutRedirectTarget,
    SessionProvider,
} from "../src/client/SessionProvider";
import { useSession } from "../src/client/useSession";

describe("SessionProvider logout redirect selection", () => {
    it("prefers redirect_url when provided", () => {
        expect(
            resolveLogoutRedirectTarget("/runtime/apps/my-app", {
                redirect_url: "/runtime/apps/my-app/logged-out",
                logout_url: "https://cluster.test/api/auth/logout",
            })
        ).toBe("/runtime/apps/my-app/logged-out");
    });

    it("falls back to the app logged-out page instead of logout_url", () => {
        expect(
            resolveLogoutRedirectTarget("/runtime/apps/my-app", {
                logout_url: "https://cluster.test/api/auth/logout",
            })
        ).toBe("/runtime/apps/my-app/logged-out");
    });

    it("uses the root logged-out page when no base path is present", () => {
        expect(resolveLogoutRedirectTarget("", {})).toBe("/logged-out");
    });
});

describe("isTrustedFrontChannelUrl", () => {
    it("accepts an absolute https platform URL on any origin", () => {
        // Cross-origin to the app: the front-channel endpoint lives on the
        // platform API host, which may differ from the app host.
        expect(
            isTrustedFrontChannelUrl(
                "https://cluster.test/api/auth/logout/front-channel?redirect_uri=x"
            )
        ).toBe(true);
    });

    it("accepts a plain http absolute URL (dev-local split origin)", () => {
        expect(
            isTrustedFrontChannelUrl("http://localhost:8000/api/auth/logout/front-channel")
        ).toBe(true);
    });

    it("accepts a safe relative path", () => {
        expect(isTrustedFrontChannelUrl("/api/auth/logout/front-channel")).toBe(true);
    });

    it("rejects protocol-relative URLs", () => {
        expect(isTrustedFrontChannelUrl("//evil.example.com/logout")).toBe(false);
    });

    it("rejects javascript: and empty values", () => {
        expect(isTrustedFrontChannelUrl("javascript:alert(1)")).toBe(false);
        expect(isTrustedFrontChannelUrl("")).toBe(false);
    });
});

describe("SessionProvider logout navigation", () => {
    let fetchSpy: ReturnType<typeof vi.fn>;
    let assignSpy: ReturnType<typeof vi.fn>;
    let originalLocation: Location;

    const ANON_SESSION = {
        user_id: null,
        name: "Anonymous",
        roles: [],
        is_authenticated: false,
        expires_at: null,
    };

    beforeEach(() => {
        fetchSpy = vi.fn();
        vi.stubGlobal("fetch", fetchSpy);
        // App is served from a different origin than the platform API host,
        // so the front-channel URL is cross-origin — exactly the case the
        // same-origin guard would wrongly reject.
        assignSpy = vi.fn();
        originalLocation = window.location;
        Object.defineProperty(window, "location", {
            configurable: true,
            value: { origin: "https://app.cluster.test", assign: assignSpy },
        });
    });

    afterEach(() => {
        vi.unstubAllGlobals();
        Object.defineProperty(window, "location", {
            configurable: true,
            value: originalLocation,
        });
    });

    function mockLogoutResponse(body: Record<string, unknown>) {
        fetchSpy.mockImplementation((_url: string, init?: RequestInit) => {
            const payload = init?.method === "POST" ? body : ANON_SESSION;
            return Promise.resolve(
                new Response(JSON.stringify(payload), {
                    status: 200,
                    headers: { "content-type": "application/json" },
                })
            );
        });
    }

    function CaptureLogout(props: { onReady: (logout: () => Promise<void>) => void }) {
        const ctx = useSession();
        useEffect(() => {
            if (!ctx.loading) props.onReady(ctx.logout);
        }, [ctx.loading]);
        return null;
    }

    async function renderAndLogout(): Promise<void> {
        let logoutFn: (() => Promise<void>) | undefined;
        await act(async () => {
            render(
                <SessionProvider refreshInterval={0}>
                    <CaptureLogout onReady={(l) => (logoutFn = l)} />
                </SessionProvider>
            );
        });
        await waitFor(() => expect(logoutFn).toBeTruthy());
        await act(async () => {
            await logoutFn!();
        });
    }

    it("navigates to the cross-origin front-channel logout URL (ENG-6911)", async () => {
        const frontChannel =
            "https://cluster.test/api/auth/logout/front-channel?redirect_uri=https%3A%2F%2Fapp.cluster.test%2F";
        mockLogoutResponse({
            front_channel_logout_url: frontChannel,
            // Legacy fields the old client used — must NOT win over front-channel.
            logout_url: "https://cluster.test/api/auth/logout",
            redirect_url: "/logged-out",
        });

        await renderAndLogout();

        expect(assignSpy).toHaveBeenCalledWith(frontChannel);
    });

    it("falls back to redirect_url when no front-channel URL is present", async () => {
        mockLogoutResponse({ redirect_url: "/logged-out" });

        await renderAndLogout();

        expect(assignSpy).toHaveBeenCalledWith("/logged-out");
    });
});


describe("SessionProvider snake_case → camelCase translation", () => {
    let fetchSpy: ReturnType<typeof vi.fn>;

    beforeEach(() => {
        fetchSpy = vi.fn();
        vi.stubGlobal("fetch", fetchSpy);
    });

    afterEach(() => {
        vi.unstubAllGlobals();
    });

    /**
     * Capture the session value the SessionProvider hands to children via
     * useSession(). Any consumer reading `session.isAuthenticated`,
     * `session.userId`, or `session.workroomId` (camelCase) relies on this
     * translation. The Python /session endpoint emits snake_case
     * (`is_authenticated`, `user_id`, `workroom_id`); a regression that
     * reverted to passing the raw response would silently break every
     * client predicate that depends on the camelCase shape — including the
     * `kz-ext create app` template's AuthGuard local-dev bypass
     * (`session.isAuthenticated === false && session.name === "Anonymous"`).
     */
    function Capture(props: { onSession: (s: unknown) => void }) {
        const ctx = useSession();
        useEffect(() => {
            if (!ctx.loading) props.onSession(ctx.session);
        }, [ctx.loading, ctx.session]);
        return null;
    }

    it("translates the canonical anonymous /session payload to camelCase", async () => {
        // What the Python /session router emits under USE_AUTH=false.
        fetchSpy.mockResolvedValue(
            new Response(
                JSON.stringify({
                    user_id: null,
                    email: null,
                    name: "Anonymous",
                    roles: [],
                    workroom_id: null,
                    workroom_role: null,
                    is_authenticated: false,
                    expires_at: null,
                }),
                { status: 200, headers: { "content-type": "application/json" } },
            ),
        );

        const captured: Array<unknown> = [];
        await act(async () => {
            render(
                <SessionProvider refreshInterval={0}>
                    <Capture onSession={(s) => captured.push(s)} />
                </SessionProvider>,
            );
        });

        await waitFor(() => expect(captured.length).toBeGreaterThan(0));
        const session = captured[captured.length - 1] as Record<string, unknown>;

        // The shape consumers depend on. Any drift here cascades into the
        // template's local-dev render path failing silently.
        expect(session).toMatchObject({
            userId: null,
            email: null,
            name: "Anonymous",
            roles: [],
            workroomId: null,
            isAuthenticated: false,
        });

        // And the snake_case keys must NOT have leaked through.
        expect(session).not.toHaveProperty("is_authenticated");
        expect(session).not.toHaveProperty("user_id");
        expect(session).not.toHaveProperty("workroom_id");
    });

    it("translates an authenticated payload to camelCase", async () => {
        fetchSpy.mockResolvedValue(
            new Response(
                JSON.stringify({
                    user_id: "u-123",
                    email: "alice@example.com",
                    name: "Alice",
                    roles: ["developer"],
                    workroom_id: "wr-7",
                    is_authenticated: true,
                    expires_at: 9_999_999_999,
                }),
                { status: 200, headers: { "content-type": "application/json" } },
            ),
        );

        const captured: Array<unknown> = [];
        await act(async () => {
            render(
                <SessionProvider refreshInterval={0}>
                    <Capture onSession={(s) => captured.push(s)} />
                </SessionProvider>,
            );
        });

        await waitFor(() => expect(captured.length).toBeGreaterThan(0));
        const session = captured[captured.length - 1] as Record<string, unknown>;

        expect(session).toMatchObject({
            userId: "u-123",
            email: "alice@example.com",
            name: "Alice",
            roles: ["developer"],
            workroomId: "wr-7",
            isAuthenticated: true,
            expiresAt: 9_999_999_999,
        });
    });
});
