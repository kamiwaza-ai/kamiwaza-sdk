import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { extractIdentityStrict } from "../src/server/identity";
import { MisboundAuthError } from "../src/server/errors";

// TS-M2-22 (happy-path), TS-M2-23 (missing-user-id), TS-M2-24 (missing-workroom),
// TS-M2-21-mirror (global-sentinel) — the same canonical vectors consumed by
// the Python lib and the Go reference. If a vector fails here while passing
// in Python, the implementations have diverged; fix the implementation, not
// the vector.

const VECTORS_PATH = resolve(__dirname, "../../docs/extensions/non-sdk-flow/test-vectors.json");

interface HappyVector {
    case: string;
    headers: Record<string, string>;
    expected_identity: {
        user_id: string;
        email: string | null;
        name: string | null;
        roles: string[];
        system_high: string | null;
        workroom_id: string;
        workroom_role: string | null;
        request_id: string | null;
    };
}

interface FailureVector {
    case: string;
    headers: Record<string, string>;
    should_fail_class: string;
}

type Vector = HappyVector | FailureVector;

function isFailure(v: Vector): v is FailureVector {
    return "should_fail_class" in v;
}

const vectors: Vector[] = JSON.parse(readFileSync(VECTORS_PATH, "utf-8"));

// Map snake_case JSON keys onto camelCase TS Identity fields.
function toExpectedTsShape(expected: HappyVector["expected_identity"]) {
    return {
        userId: expected.user_id,
        email: expected.email,
        name: expected.name,
        roles: expected.roles,
        systemHigh: expected.system_high,
        workroomId: expected.workroom_id,
        workroomRole: expected.workroom_role,
        requestId: expected.request_id,
    };
}

describe("canonical test-vectors parity (TS ↔ Py ↔ Go)", () => {
    for (const v of vectors) {
        if (isFailure(v)) {
            it(`${v.case}: throws ${v.should_fail_class}`, () => {
                const headers = new Headers(v.headers);
                if (v.should_fail_class === "misbound_auth") {
                    expect(() => extractIdentityStrict(headers)).toThrow(MisboundAuthError);
                } else {
                    throw new Error(`Unrecognized failure class ${v.should_fail_class!}`);
                }
            });
        } else {
            it(`${v.case}: matches expected_identity`, () => {
                const headers = new Headers(v.headers);
                const identity = extractIdentityStrict(headers);
                const expected = toExpectedTsShape(v.expected_identity);
                // Project off isAuthenticated — TS-specific niceness, not in vectors.
                const projected = {
                    userId: identity.userId,
                    email: identity.email,
                    name: identity.name,
                    roles: identity.roles,
                    systemHigh: identity.systemHigh,
                    workroomId: identity.workroomId,
                    workroomRole: identity.workroomRole,
                    requestId: identity.requestId,
                };
                expect(projected).toEqual(expected);
            });
        }
    }
});
