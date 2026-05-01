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
    createLocalDevAuthMiddleware,
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

    it("clears spoofed envelope headers before bridging (round-6 codex P2)", () => {
        // A request without `authorization` but with client-supplied
        // envelope headers (e.g. `x-user-system-high: 1`,
        // `x-user-roles: admin,owner`, `x-user-workroom-role: admin`)
        // must not have those spoofed values forwarded to the backend.
        // Round-6 review: the bridge previously preserved them because
        // it started from `new Headers(incoming)` and only set a subset.
        const token = makeJwt({ sub: "user-bridge", email: "u@x" });
        process.env[GATE] = "1";
        process.env[TOKEN] = token;

        const incoming = new Headers({
            // No `authorization` — the bridge will activate.
            "x-user-id": "spoof-user",
            "x-user-email": "evil@example.com",
            "x-user-name": "Spoof",
            "x-user-roles": "admin,owner",
            "x-user-system-high": "1",
            "x-user-workroom-role": "admin",
            "x-user-workroom-id": "wr-spoof",
            "x-workroom-id": "wr-spoof",
            "x-user-signature": "fake-sig",
            "x-user-signature-ts": "12345",
            "x-user-id-extra": "should-be-preserved",
        });

        const out = _buildBridgedHeaders(incoming);

        // Authoritative bridged values
        expect(out.get("authorization")).toBe(`Bearer ${token}`);
        expect(out.get("x-user-id")).toBe("user-bridge");
        expect(out.get("x-user-email")).toBe("u@x");
        // x-workroom-id falls back to JWT sub (no override env set)
        expect(out.get("x-workroom-id")).toBe("user-bridge");

        // Spoofed envelope fields the JWT didn't set must be CLEARED,
        // not preserved from the incoming request.
        expect(out.get("x-user-roles")).toBeNull();
        expect(out.get("x-user-system-high")).toBeNull();
        expect(out.get("x-user-workroom-role")).toBeNull();
        expect(out.get("x-user-workroom-id")).toBeNull();
        expect(out.get("x-user-signature")).toBeNull();
        expect(out.get("x-user-signature-ts")).toBeNull();
        expect(out.get("x-auth-token")).toBeNull();

        // Non-envelope headers must be preserved
        expect(out.get("x-user-id-extra")).toBe("should-be-preserved");
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

describe("createLocalDevAuthMiddleware factory diagnostics", () => {
    let infoSpy: ReturnType<typeof vi.spyOn>;
    let originalEnv: Record<string, string | undefined>;

    beforeEach(() => {
        // Snapshot/restore env (round-12 review Comprehensive M3 — earlier
        // describe blocks save and restore originalEnv; this one didn't,
        // so a future test added here could leak GATE/TOKEN/WORKROOM.
        originalEnv = {
            [GATE]: process.env[GATE],
            [TOKEN]: process.env[TOKEN],
            [WORKROOM]: process.env[WORKROOM],
        };
        delete process.env[GATE];
        delete process.env[TOKEN];
        delete process.env[WORKROOM];
        _resetWarnOnceState();
        infoSpy = vi.spyOn(console, "info").mockImplementation(() => {});
    });

    afterEach(() => {
        infoSpy.mockRestore();
        for (const [key, value] of Object.entries(originalEnv)) {
            if (value === undefined) {
                delete process.env[key];
            } else {
                process.env[key] = value;
            }
        }
    });

    it("logs the captured user_id at factory creation under --auth", () => {
        // PR #87 round-10 H4 → round-11 review (Claude M5) — Next.js
        // dev HMR may not re-instantiate the middleware factory when
        // the bearer rotates, so a stale token can silently persist
        // across `kz-ext login --use other`. The factory logs the
        // resolved bridge ``user_id`` once at creation; this test
        // pins the diagnostic so a future refactor that drops the
        // log (e.g., moving the decode into the request closure)
        // fails loudly.
        process.env[GATE] = "1";
        process.env[TOKEN] = makeJwt({ sub: "u-bridge-debug" });

        createLocalDevAuthMiddleware();

        expect(infoSpy).toHaveBeenCalled();
        const message = infoSpy.mock.calls[0]?.[0] ?? "";
        expect(message).toContain("local-dev-auth");
        expect(message).toContain("user_id=u-bridge-debug");
    });

    it("does NOT log when gate is off (production behavior)", () => {
        // Gate unset → bridge is pass-through → no diagnostic chatter.
        // Important: this log is not meant to fire in any prod path.
        process.env[TOKEN] = makeJwt({ sub: "u-prod" });
        // GATE intentionally unset.

        createLocalDevAuthMiddleware();
        expect(infoSpy).not.toHaveBeenCalled();
    });

    it("does NOT log when gate is on but token is missing", () => {
        // The factory's "warn + no-op" path doesn't reach the log
        // (which fires only when there's a real token to capture).
        process.env[GATE] = "1";

        createLocalDevAuthMiddleware();
        expect(infoSpy).not.toHaveBeenCalled();
    });
});
