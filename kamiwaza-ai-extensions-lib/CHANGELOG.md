# Changelog — `@kamiwaza-ai/extensions-lib` (TypeScript runtime)

Versions follow semver. Distributed alongside `kamiwaza-sdk` but versioned
independently.

## [0.4.0] — 2026-04-30 (ENG-4318)

### Added

* New subpath export `@kamiwaza-ai/extensions-lib/local-dev-auth` with
  `createLocalDevAuthMiddleware()` — Next.js middleware that bridges the
  developer's identity from `kz-ext login` into a running extension when
  `kz-ext dev local --auth` sets `KZ_EXT_DEV_LOCAL_AUTH=1` and
  `KAMIWAZA_BEARER_TOKEN` on the container. Synthesizes the platform's
  forwarded-auth envelope (`authorization`, `x-user-id`, `x-user-email`,
  `x-user-name`, `x-user-roles`, `x-workroom-id`) from the bearer's JWT
  claims so the rest of the extension code (proxy, identity extractor,
  session router, AuthGuard) sees the same input shape it gets in
  production.
* The new export deliberately ships under its own subpath — importing
  `@kamiwaza-ai/extensions-lib/server` does NOT pull in `next/server`,
  preserving the package's "Next is an optional peer dep" contract for
  consumers that only use `fetchModels` / `createProxyHandlers` /
  `extractIdentity`.

### Behavior notes

* The bridge is a no-op pass-through when the gate env var is unset
  (production behaviour preserved).
* When the gate is set, all forwarded-auth envelope headers on the
  inbound request are cleared on EVERY path before any synthesized
  values are injected — defends against client-supplied spoofs of
  fields we don't bridge (e.g. `x-user-system-high`,
  `x-user-workroom-role`). Round-13 review (codex P2): the prior
  implementation skipped sanitization when an inbound `Authorization`
  was set, leaving an envelope-spoof bypass; the fix sanitizes
  unconditionally under `--auth` (no platform gateway in dev → spoofs
  never have a legitimate source).
* Inbound requests that already carry an `Authorization` header keep
  that header intact (real platform identity always wins over the
  bridge — defense in depth if the gate accidentally bleeds into a
  non-dev environment) but the OTHER envelope headers are still
  sanitized so spoofs can't survive alongside the inbound bearer.
* `KAMIWAZA_DEV_WORKROOM_ID` env var optionally overrides the
  synthesized `x-workroom-id`; otherwise it defaults to the JWT `sub`
  so the strict identity path succeeds and `workroom_id` is stable
  per-developer.
* JWT decoding is signature-less (the platform validates the bearer at
  request time) and UTF-8-aware so non-ASCII claims (`name`, `email`)
  round-trip correctly instead of mojibake.
* Warnings ("gate enabled but token unset" / "token undecodable") are
  throttled to once-per-process to avoid log spam under N parallel
  request chunks on a Next.js page.

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
