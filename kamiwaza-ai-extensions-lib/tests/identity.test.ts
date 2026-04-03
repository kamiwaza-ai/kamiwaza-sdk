import { describe, it, expect } from "vitest";
import { extractIdentity } from "../src/server/identity";

describe("extractIdentity", () => {
    it("returns full identity from headers", () => {
        const headers = new Headers({
            "x-user-id": "usr-123",
            "x-user-email": "alice@example.com",
            "x-user-name": "Alice",
            "x-user-roles": "admin,user",
            "x-workroom-id": "wrk-456",
        });

        const identity = extractIdentity(headers);

        expect(identity).not.toBeNull();
        expect(identity!.userId).toBe("usr-123");
        expect(identity!.email).toBe("alice@example.com");
        expect(identity!.name).toBe("Alice");
        expect(identity!.roles).toEqual(["admin", "user"]);
        expect(identity!.workroomId).toBe("wrk-456");
        expect(identity!.isAuthenticated).toBe(true);
    });

    it("returns null when no user id header", () => {
        const headers = new Headers({
            "content-type": "application/json",
        });

        const identity = extractIdentity(headers);
        expect(identity).toBeNull();
    });

    it("handles missing optional headers", () => {
        const headers = new Headers({
            "x-user-id": "usr-123",
        });

        const identity = extractIdentity(headers);

        expect(identity).not.toBeNull();
        expect(identity!.userId).toBe("usr-123");
        expect(identity!.email).toBeNull();
        expect(identity!.name).toBeNull();
        expect(identity!.roles).toEqual([]);
        expect(identity!.workroomId).toBeNull();
        expect(identity!.isAuthenticated).toBe(true);
    });

    it("parses roles with whitespace", () => {
        const headers = new Headers({
            "x-user-id": "usr-123",
            "x-user-roles": " admin , user , viewer ",
        });

        const identity = extractIdentity(headers);
        expect(identity!.roles).toEqual(["admin", "user", "viewer"]);
    });

    it("filters empty roles from extra commas", () => {
        const headers = new Headers({
            "x-user-id": "usr-123",
            "x-user-roles": "admin,,user,",
        });

        const identity = extractIdentity(headers);
        expect(identity!.roles).toEqual(["admin", "user"]);
    });

    it("works with ReadonlyHeaders-like objects", () => {
        const mockHeaders = {
            get(name: string): string | null {
                const map: Record<string, string> = {
                    "x-user-id": "usr-123",
                    "x-user-name": "Alice",
                };
                return map[name] ?? null;
            },
        };

        const identity = extractIdentity(mockHeaders);
        expect(identity).not.toBeNull();
        expect(identity!.userId).toBe("usr-123");
        expect(identity!.name).toBe("Alice");
    });
});
