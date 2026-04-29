import { describe, it, expect } from "vitest";
import {
    KamiwazaRuntimeError,
    MisboundAuthError,
    UnexpectedContextError,
    OutOfEnvelopeAccessError,
    PlatformOutageError,
    StreamInterruptedError,
} from "../src/server/errors";

// TS-M2-27: errors module exports the canonical hierarchy with className constants.
//
// The five-class hierarchy mirrors kamiwaza_extensions_lib.errors (Python).
// className constants are the canonical keys consumed by exception_names.json
// + DoctorChecker — divergence here breaks UAC-9d coverage.
describe("error hierarchy", () => {
    it.each([
        ["MisboundAuthError", MisboundAuthError, "misbound_auth"],
        ["UnexpectedContextError", UnexpectedContextError, "unexpected_context"],
        ["OutOfEnvelopeAccessError", OutOfEnvelopeAccessError, "out_of_envelope_access"],
        ["PlatformOutageError", PlatformOutageError, "platform_outage"],
        ["StreamInterruptedError", StreamInterruptedError, "stream_interrupted"],
    ])("%s extends KamiwazaRuntimeError and has className %s", (_name, Cls, expected) => {
        const e = new Cls("boom");
        expect(e).toBeInstanceOf(KamiwazaRuntimeError);
        expect(e).toBeInstanceOf(Error);
        expect((Cls as unknown as { className: string }).className).toBe(expected);
    });

    it("KamiwazaRuntimeError preserves Error semantics", () => {
        const e = new KamiwazaRuntimeError("reason");
        expect(e.message).toBe("reason");
        expect(e.name).toBe("KamiwazaRuntimeError");
        expect(e instanceof Error).toBe(true);
    });

    it("subclasses preserve their name on the instance", () => {
        const e = new MisboundAuthError("missing X-User-Id");
        expect(e.name).toBe("MisboundAuthError");
        expect(e.message).toContain("X-User-Id");
    });
});
