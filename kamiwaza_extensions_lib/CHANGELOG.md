# Changelog — `kamiwaza-extensions-lib` (Python runtime)

All notable changes to this runtime library are documented here. Versions
follow semver. The library is distributed alongside `kamiwaza-sdk` but
versioned independently — extension authors pin against the `[lib]` minor
range in `requirements.txt`.

## [0.3.0] — 2026-04-29 (D210 M2)

### Added

* **Canonical test-vector parity.** `extract_identity` is now contract-tested
  against `docs/extensions/non-sdk-flow/test-vectors.json` — the same JSON
  fixtures consumed by the TypeScript runtime lib and the Go reference
  extension. Divergence between language implementations now fails the
  parity test in all three languages simultaneously. (ENG-3892, T2.9.)

### Documentation

* `docs/extensions/non-sdk-flow.md` is the new canonical contract for
  non-Python/TS extension authors. The Python lib aligns with that contract:
  header parsing only (no HMAC, no shared secret, no canonicalization, no
  TTL check on the extension side). (ENG-3891, T2.7.)

### Notes

* No behavior change to `extract_identity` itself — M1 (ENG-3885) shipped
  the contract this release pins. The 0.2 series was reserved for the
  M1 work; 0.3 is the version-bump that signals "now contract-tested
  cross-language."

## [0.2.0] — 2026-04-25 (D210 M1)

### Added

* Runtime-lib exception hierarchy: `KamiwazaRuntimeError` base + four
  canonical subclasses (`MisboundAuthError`, `UnexpectedContextError`,
  `OutOfEnvelopeAccessError`, `PlatformOutageError`). Each carries a
  `class_name` constant and matching entry in
  `kamiwaza_extensions_lib/exception_names.json`. (ENG-3885, UAC-9d.)
* `extract_identity(headers)` strict header-parser raising
  `MisboundAuthError` on missing `X-User-Id` or `X-Workroom-Id`. Companion
  permissive `identity_from_headers` returns whatever fields are present
  without raising. (ENG-3885.)
* `Identity` Pydantic model with all envelope fields except `X-Auth-Token`
  (deliberately excluded from the model so `.model_dump()` doesn't leak
  the bearer credential into logs/metrics).
* `anonymous_identity()` for the canonical `USE_AUTH=false` placeholder
  shape (`name="Anonymous"`, `is_authenticated=False`).

## [0.1.0] — 2026-04-08

* Initial release alongside `kz-ext` Phase 1.
