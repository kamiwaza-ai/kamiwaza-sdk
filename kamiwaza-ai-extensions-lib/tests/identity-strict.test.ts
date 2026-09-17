import { describe, it, expect } from "vitest";
import { extractIdentityStrict } from "../src/server/identity";
import { MisboundAuthError } from "../src/server/errors";

// TS-M2-22..24: strict mirror of Python extract_identity.
// Missing X-User-Id or X-Workroom-Id → MisboundAuthError.

describe("extractIdentityStrict", () => {
    it("returns identity for full envelope (TS-M2-22)", () => {
        const headers = new Headers({
            "x-user-id": "u1",
            "x-user-email": "alice@example.com",
            "x-user-name": "Alice",
            "x-user-roles": "member,editor",
            "x-user-system-high": "U",
            "x-workroom-id": "w1",
            "x-user-workroom-role": "editor",
            "x-request-id": "req-abc",
        });
        const identity = extractIdentityStrict(headers);
        expect(identity.userId).toBe("u1");
        expect(identity.workroomId).toBe("w1");
        expect(identity.systemHigh).toBe("U");
        expect(identity.workroomRole).toBe("editor");
        expect(identity.requestId).toBe("req-abc");
        expect(identity.isAuthenticated).toBe(true);
    });

    it("throws MisboundAuthError when X-User-Id missing (TS-M2-23)", () => {
        const headers = new Headers({ "x-workroom-id": "w1" });
        expect(() => extractIdentityStrict(headers)).toThrow(MisboundAuthError);
        expect(() => extractIdentityStrict(headers)).toThrow(/X-User-Id/i);
    });

    it("throws MisboundAuthError when X-Workroom-Id missing (TS-M2-24)", () => {
        const headers = new Headers({ "x-user-id": "u1" });
        expect(() => extractIdentityStrict(headers)).toThrow(MisboundAuthError);
        expect(() => extractIdentityStrict(headers)).toThrow(/X-Workroom-Id/i);
    });

    it("throws when X-User-Id is whitespace-only (regression: empty != missing)", () => {
        const headers = new Headers({
            "x-user-id": "   ",
            "x-workroom-id": "w1",
        });
        expect(() => extractIdentityStrict(headers)).toThrow(MisboundAuthError);
    });
});

// TS-M2-26: Identity has the 3 missing fields added (systemHigh, workroomRole, requestId).
describe("Identity shape", () => {
    it("includes the 9 envelope fields per design §4.2.7", () => {
        const headers = new Headers({
            "x-user-id": "u1",
            "x-workroom-id": "w1",
        });
        const identity = extractIdentityStrict(headers);
        // Field presence — actual values not the focus here.
        const keys = Object.keys(identity).sort();
        expect(keys).toEqual(
            [
                "email",
                "isAuthenticated",
                "name",
                "requestId",
                "roles",
                "systemHigh",
                "userId",
                "workroomId",
                "workroomRole",
            ].sort(),
        );
    });
});
