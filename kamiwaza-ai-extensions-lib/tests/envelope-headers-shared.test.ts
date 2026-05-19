/**
 * PR #87 round-10 review (Comprehensive H) — the local-dev-auth bridge
 * MUST clear every envelope header that ``proxy.ts`` is willing to
 * forward, otherwise an inbound spoof of (e.g.) ``x-user-system-high``
 * survives the bridge's clear-and-synthesize cycle.
 *
 * Round-10 collapsed both lists onto a shared
 * ``ENVELOPE_AUTH_HEADERS`` constant so the drift surface is gone.
 * This test pins the contract: if a future contributor adds an
 * envelope header to one importer without the other, the import-side
 * mismatch makes this assertion fail (or the missing import does at
 * the type/build level).
 */
import { describe, expect, it } from "vitest";

import { ENVELOPE_AUTH_HEADERS } from "../src/_shared/envelopeHeaders";
import { _buildBridgedHeaders } from "../src/local-dev-auth";

describe("envelope auth headers shared constant", () => {
    it("contains only auth-bearing headers (no transport)", () => {
        for (const h of ENVELOPE_AUTH_HEADERS) {
            expect(h.startsWith("x-") || h === "authorization").toBe(true);
        }
        // Transport / tracing headers are intentionally NOT here — they
        // belong only on proxy.ts's forward-list.
        expect(ENVELOPE_AUTH_HEADERS as readonly string[]).not.toContain("cookie");
        expect(ENVELOPE_AUTH_HEADERS as readonly string[]).not.toContain("content-type");
        expect(ENVELOPE_AUTH_HEADERS as readonly string[]).not.toContain("x-request-id");
    });

    it("bridge clears every shared envelope header before synthesis", () => {
        // Build a hostile request setting every envelope header EXCEPT
        // ``authorization`` to a spoof value, then run it through the
        // bridge with a valid bearer. The bridge bypasses synthesis
        // entirely if ``authorization`` is already present (defense in
        // depth — real platform identity wins), so the meaningful
        // spoof-clearing path is the "no inbound auth" case. After
        // synthesis, NONE of the original SPOOF values survive on the
        // bridge's clear-list.
        const headers = new Headers();
        for (const h of ENVELOPE_AUTH_HEADERS) {
            if (h === "authorization") continue;
            headers.set(h, "SPOOF");
        }
        // Minimum 3-segment JWT shape with sub=u-bridge in payload.
        const payload = Buffer.from(
            JSON.stringify({ sub: "u-bridge", email: "b@x" }),
            "utf-8",
        )
            .toString("base64")
            .replace(/=+$/, "")
            .replace(/\+/g, "-")
            .replace(/\//g, "_");
        const jwt = `h.${payload}.s`;
        const bridged = _buildBridgedHeaders(headers, {
            enabled: true,
            token: jwt,
            workroomOverride: null,
        });
        for (const h of ENVELOPE_AUTH_HEADERS) {
            expect(bridged.get(h) || "").not.toBe("SPOOF");
        }
        // Sanity: synthesis happened.
        expect(bridged.get("x-user-id")).toBe("u-bridge");
    });
});
