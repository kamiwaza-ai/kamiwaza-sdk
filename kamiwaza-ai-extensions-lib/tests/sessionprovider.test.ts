import { describe, it, expect } from "vitest";
import { resolveLogoutRedirectTarget } from "../src/client/SessionProvider";

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
