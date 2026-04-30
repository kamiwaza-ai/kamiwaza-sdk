# Changelog — `@kamiwaza-ai/extensions-lib` (TypeScript runtime)

Versions follow semver. Distributed alongside `kamiwaza-sdk` but versioned
independently.

## [0.3.0] — 2026-04-29 (D210 M2)

### Added

* `extractIdentityStrict(headers)` — strict mirror of Python's
  `extract_identity`. Throws `MisboundAuthError` when `X-User-Id` or
  `X-Workroom-Id` is missing or whitespace-only. The permissive
  `extractIdentity` (returning `null`) is preserved unchanged for
  backward-compat. (ENG-3893, T2.10.)
* Canonical error hierarchy at `@kamiwaza-ai/extensions-lib/server`:
  `KamiwazaRuntimeError` base + `MisboundAuthError`,
  `UnexpectedContextError`, `OutOfEnvelopeAccessError`,
  `PlatformOutageError`, `StreamInterruptedError`. Each subclass carries
  a static `className` matching `kamiwaza_extensions/exception_names.json`
  so cross-language error pipelines stay aligned.
* `Identity` gains 3 missing fields to match the Python contract:
  `systemHigh`, `workroomRole`, `requestId`. Existing callers reading the
  6-field shape still type-check (TypeScript widens null-allowed fields
  without breaking).
* Canonical test-vector parity. The TypeScript suite now consumes
  `docs/extensions/non-sdk-flow/test-vectors.json` directly, so a vector
  failing here while passing in Python (or vice versa) is an
  implementation drift bug.

### Notes

* No breaking changes for v0.2 callers using only `extractIdentity` —
  `Identity`'s new fields are nullable, so code reading the v0.2 subset
  continues to work.
* **Subtle behavior change in `extractIdentity`** (PR #86 review M1):
  v0.2 returned `headers.get(...)` verbatim for `email` / `name` /
  `workroomId`; whitespace-only header values surfaced as the literal
  whitespace string. v0.3 strips and treats whitespace-only as missing
  (returns `null`), matching the Python contract. This closes a subtle
  spoofing avenue (`X-User-Id: "   "` would have passed a naive truthy
  check) but may surprise callers that relied on the raw passthrough.

## [0.2.0] — 2026-04-25 (D210 M1)

### Added

* Initial server-side `extractIdentity(headers): Identity | null`.
* `createProxyHandlers` for App Router proxy routes.
* `SessionProvider` + `AuthGuard` + `useSession` client primitives.
* `fetchModels` model-discovery helper.
