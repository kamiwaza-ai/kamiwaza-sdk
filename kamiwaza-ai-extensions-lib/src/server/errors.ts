/**
 * Canonical runtime-lib error hierarchy. Mirrors
 * ``kamiwaza_extensions_lib.errors`` (Python).
 *
 * Each subclass carries a static ``className`` that matches the canonical
 * identifier in ``kamiwaza_extensions/exception_names.json``, which the
 * CLI's ``DoctorChecker`` and the platform-side audit pipeline both
 * consume. If you rename one, rename it everywhere.
 */

export class KamiwazaRuntimeError extends Error {
    static readonly className = "kamiwaza_runtime_error";

    constructor(message?: string) {
        super(message);
        // Preserve subclass name on the instance so stack traces and
        // error.name reflect the actual class, not "Error".
        this.name = new.target.name;
    }
}

export class MisboundAuthError extends KamiwazaRuntimeError {
    static readonly className = "misbound_auth";
}

export class UnexpectedContextError extends KamiwazaRuntimeError {
    static readonly className = "unexpected_context";
}

export class OutOfEnvelopeAccessError extends KamiwazaRuntimeError {
    static readonly className = "out_of_envelope_access";
}

export class PlatformOutageError extends KamiwazaRuntimeError {
    static readonly className = "platform_outage";
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
    static readonly className = "stream_interrupted";
}
