/**
 * Canonical runtime-lib error hierarchy. Mirrors
 * ``kamiwaza_extensions_lib.errors`` (Python).
 *
 * Each subclass carries a static ``className`` that matches the canonical
 * identifier in ``kamiwaza_extensions_lib/exception_names.json`` (PR-86 H4
 * — earlier comment had the wrong path), which the CLI's ``DoctorChecker``
 * and the platform-side audit pipeline both consume. If you rename one,
 * rename it everywhere — including the JSON registry.
 */

// Explicit ``: string`` annotations on each ``className`` static keep the
// type wide enough for subclass overrides. Without the annotation,
// TypeScript infers each value as its literal type
// (e.g. ``"kamiwaza_runtime_error"``) and TS2417 fires when a subclass
// tries to override the static with a different literal. Runtime values
// are unchanged; the JSON registry still pins each class_name string.

export class KamiwazaRuntimeError extends Error {
    static readonly className: string = "kamiwaza_runtime_error";

    constructor(message?: string) {
        super(message);
        // Preserve subclass name on the instance so stack traces and
        // error.name reflect the actual class, not "Error".
        this.name = new.target.name;
    }
}

export class MisboundAuthError extends KamiwazaRuntimeError {
    static readonly className: string = "misbound_auth";
}

export class UnexpectedContextError extends KamiwazaRuntimeError {
    static readonly className: string = "unexpected_context";
}

export class OutOfEnvelopeAccessError extends KamiwazaRuntimeError {
    static readonly className: string = "out_of_envelope_access";
}

export class PlatformOutageError extends KamiwazaRuntimeError {
    static readonly className: string = "platform_outage";
}

/**
 * Upstream stream failed *after* response bytes were committed downstream.
 *
 * Surfaces from ``streamWithRefresh`` (ENG-3895). Distinct from
 * ``PlatformOutageError`` because retry is impossible — the HTTP status was
 * already sent. Extension SDKs should map a connection close on a streaming
 * response to this class.
 */
export class StreamInterruptedError extends KamiwazaRuntimeError {
    static readonly className: string = "stream_interrupted";
}
