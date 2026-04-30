/**
 * Tests for createLocalDevAuthMiddleware (ENG-4318).
 *
 * Covers test scenarios TS-16 (gate-off no-op), TS-17 (gate-on without
 * token = warn + no-op, NOT a synthesized fake identity), TS-18 (happy path
 * envelope synthesis), TS-19 (roles parsing), TS-20 (malformed JWT).
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
    _buildBridgedHeaders,
    _decodeJwt,
    _resetWarnOnceState,
} from "../src/local-dev-auth/index";

const GATE = "KZ_EXT_DEV_LOCAL_AUTH";
const TOKEN = "KAMIWAZA_BEARER_TOKEN";
const WORKROOM = "KAMIWAZA_DEV_WORKROOM_ID";

function makeJwt(claims: Record<string, unknown>): string {
    // UTF-8-encode each segment before base64url so the test fixture
    // round-trips non-ASCII claims correctly. JWT canonical encoding
    // (RFC 7519) requires UTF-8 — using btoa(JSON.stringify(...))
    // would silently truncate multi-byte chars.
    const enc = (obj: unknown): string => {
        const bytes = new TextEncoder().encode(JSON.stringify(obj));
        let binary = "";
        for (const b of bytes) binary += String.fromCharCode(b);
        return btoa(binary)
            .replace(/=+$/, "")
            .replace(/\+/g, "-")
            .replace(/\//g, "_");
    };
    const header = enc({ alg: "none", typ: "JWT" });
    const payload = enc(claims);
    return `${header}.${payload}.sig`;
}

describe("_buildBridgedHeaders", () => {
    let originalEnv: NodeJS.ProcessEnv;
    let warnSpy: ReturnType<typeof vi.spyOn>;

    beforeEach(() => {
        originalEnv = { ...process.env };
        delete process.env[GATE];
        delete process.env[TOKEN];
        delete process.env[WORKROOM];
        _resetWarnOnceState();
        warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    });

    afterEach(() => {
        process.env = originalEnv;
        warnSpy.mockRestore();
        _resetWarnOnceState();
    });

    it("TS-16: returns the same headers when gate env is unset", () => {
        const incoming = new Headers({ "x-existing": "yes" });
        const out = _buildBridgedHeaders(incoming);
        expect(out).toBe(incoming); // identity comparison — true no-op
    });

    it("TS-17: gate=1 but token unset → warn and pass through (no synthesized identity)", () => {
        process.env[GATE] = "1";
        const incoming = new Headers({ "x-existing": "yes" });
        const out = _buildBridgedHeaders(incoming);
        expect(out).toBe(incoming);
        expect(warnSpy).toHaveBeenCalled();
        const msg = warnSpy.mock.calls[0]?.[0];
        expect(String(msg)).toContain(TOKEN);
    });

    it("TS-17 (variant): gate set to value other than '1' is treated as unset", () => {
        process.env[GATE] = "true";
        process.env[TOKEN] = makeJwt({ sub: "u" });
        const incoming = new Headers();
        const out = _buildBridgedHeaders(incoming);
        expect(out).toBe(incoming);
    });

    it("TS-18: gate=1 + token set → injects authorization, x-user-id, x-user-email, x-workroom-id", () => {
        const token = makeJwt({
            sub: "user-42",
            email: "alice@example.com",
            name: "Alice",
        });
        process.env[GATE] = "1";
        process.env[TOKEN] = token;

        const incoming = new Headers({ "x-existing": "preserved" });
        const out = _buildBridgedHeaders(incoming);

        expect(out).not.toBe(incoming); // new Headers instance
        expect(out.get("authorization")).toBe(`Bearer ${token}`);
        expect(out.get("x-user-id")).toBe("user-42");
        expect(out.get("x-user-email")).toBe("alice@example.com");
        expect(out.get("x-user-name")).toBe("Alice");
        // PR #87 review fix — x-workroom-id is required by strict
        // extract_identity() under KAMIWAZA_USE_AUTH=true. Defaults to JWT sub.
        expect(out.get("x-workroom-id")).toBe("user-42");
        expect(out.get("x-existing")).toBe("preserved");
    });

    it("respects KAMIWAZA_DEV_WORKROOM_ID override when set", () => {
        const token = makeJwt({ sub: "user-42" });
        process.env[GATE] = "1";
        process.env[TOKEN] = token;
        process.env[WORKROOM] = "wr-real-123";

        const out = _buildBridgedHeaders(new Headers());
        expect(out.get("x-workroom-id")).toBe("wr-real-123");
        expect(out.get("x-user-id")).toBe("user-42");
    });

    it("treats whitespace-only KAMIWAZA_DEV_WORKROOM_ID as unset", () => {
        const token = makeJwt({ sub: "user-7" });
        process.env[GATE] = "1";
        process.env[TOKEN] = token;
        process.env[WORKROOM] = "   ";

        const out = _buildBridgedHeaders(new Headers());
        expect(out.get("x-workroom-id")).toBe("user-7");
    });

    it("TS-19: parses realm_access.roles into x-user-roles", () => {
        const token = makeJwt({
            sub: "user-1",
            realm_access: { roles: ["admin", "developer"] },
        });
        process.env[GATE] = "1";
        process.env[TOKEN] = token;

        const out = _buildBridgedHeaders(new Headers());
        expect(out.get("x-user-roles")).toBe("admin,developer");
    });

    it("does not set x-user-email when claim is absent", () => {
        const token = makeJwt({ sub: "user-1" });
        process.env[GATE] = "1";
        process.env[TOKEN] = token;
        const out = _buildBridgedHeaders(new Headers());
        expect(out.get("x-user-email")).toBeNull();
        expect(out.get("x-user-name")).toBeNull();
        expect(out.get("x-user-roles")).toBeNull();
        // x-workroom-id is always set (required by strict extract_identity)
        expect(out.get("x-workroom-id")).toBe("user-1");
    });

    it("TS-20: malformed token (cannot decode) → warn + pass through", () => {
        process.env[GATE] = "1";
        process.env[TOKEN] = "not-a-jwt";

        const incoming = new Headers();
        const out = _buildBridgedHeaders(incoming);
        expect(out).toBe(incoming);
        expect(warnSpy).toHaveBeenCalled();
    });

    it("does not override an authorization header that's already present", () => {
        // Production safety: even with the gate accidentally enabled, an
        // already-authenticated request must not have its identity replaced.
        const token = makeJwt({ sub: "user-bridge" });
        process.env[GATE] = "1";
        process.env[TOKEN] = token;

        const incoming = new Headers({
            authorization: "Bearer real-platform-token",
            "x-user-id": "real-user",
        });
        const out = _buildBridgedHeaders(incoming);
        expect(out).toBe(incoming); // pass-through
    });

    it("falls back to top-level `roles` claim when realm_access is absent", () => {
        // Round-2 review Medium #14 — non-Keycloak IdPs use top-level roles.
        const token = makeJwt({
            sub: "user-1",
            roles: ["editor", "viewer"],
        });
        process.env[GATE] = "1";
        process.env[TOKEN] = token;

        const out = _buildBridgedHeaders(new Headers());
        expect(out.get("x-user-roles")).toBe("editor,viewer");
    });

    it("prefers realm_access.roles over top-level roles when both present", () => {
        const token = makeJwt({
            sub: "user-1",
            roles: ["top-level"],
            realm_access: { roles: ["realm-access"] },
        });
        process.env[GATE] = "1";
        process.env[TOKEN] = token;

        const out = _buildBridgedHeaders(new Headers());
        expect(out.get("x-user-roles")).toBe("realm-access");
    });

    it("warn-once: missing-token warning emits only once across N requests", () => {
        // Round-2 review High #9 — middleware re-read env on every request
        // and re-warned, flooding the log on a Next.js page with parallel
        // chunk fetches. Throttle to once-per-process.
        process.env[GATE] = "1";
        // No token set → triggers the missing-token warn path.
        for (let i = 0; i < 10; i++) {
            _buildBridgedHeaders(new Headers());
        }
        expect(warnSpy).toHaveBeenCalledTimes(1);
    });

    it("warn-once: undecodable-token warning emits only once across N requests", () => {
        process.env[GATE] = "1";
        process.env[TOKEN] = "not-a-jwt";
        for (let i = 0; i < 10; i++) {
            _buildBridgedHeaders(new Headers());
        }
        expect(warnSpy).toHaveBeenCalledTimes(1);
    });
});

describe("_decodeJwt", () => {
    it("TS-20: returns null on malformed input", () => {
        expect(_decodeJwt("")).toBeNull();
        expect(_decodeJwt("only-one-part")).toBeNull();
        expect(_decodeJwt("a.b")).toBeNull(); // no signature segment
        expect(_decodeJwt("a.!.c")).toBeNull(); // invalid base64
    });

    it("decodes a valid JWT payload", () => {
        const token = makeJwt({ sub: "abc", email: "x@y" });
        const claims = _decodeJwt(token);
        expect(claims).toEqual({ sub: "abc", email: "x@y" });
    });

    it("returns null when payload is not a JSON object", () => {
        const header = btoa("{}").replace(/=+$/, "");
        const payload = btoa("[]").replace(/=+$/, "");
        expect(_decodeJwt(`${header}.${payload}.sig`)).toBeNull();
    });

    it("decodes UTF-8 claims (non-ASCII name / email) without mojibake", () => {
        // PR #87 round-4 review (codex) — atob returns a Latin-1 binary
        // string; without explicit UTF-8 decoding, claims like `José` or
        // `名前` come back as mojibake and the bridge injects corrupted
        // x-user-name / x-user-email headers, silently diverging from
        // production for developers with UTF-8 identities.
        const token = makeJwt({
            sub: "user-utf8",
            email: "josé@example.com",
            name: "José 名前 🌸",
        });
        const claims = _decodeJwt(token);
        expect(claims).toEqual({
            sub: "user-utf8",
            email: "josé@example.com",
            name: "José 名前 🌸",
        });
    });
});
