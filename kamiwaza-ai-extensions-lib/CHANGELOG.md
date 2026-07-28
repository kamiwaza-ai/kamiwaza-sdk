# Changelog — `@kamiwaza-ai/extensions-lib` (TypeScript runtime)

Versions follow semver. Published to npm as a standalone package
(`@kamiwaza-ai/extensions-lib`) and versioned independently from
`kamiwaza-sdk`.

## [0.5.0] — 2026-07-28

### Added

* Dual-artifact Next.js runtime support: `withKamiwazaAppGarden()` builds
  native port and sentinel-path variants, while the packaged index and boot
  scripts relocate the path artifact to `KAMIWAZA_APP_PATH` without running
  `next build` at container start.
* Fail-closed relocation validation for hashes, occurrence counts, JSON and
  RSC payloads, symlinks, source maps, runtime paths, and residual sentinel
  bytes.
* Runtime bootstrap, same-app fetch, asset URL, runtime-config response, and
  server-side path helpers under the new `runtime`, `runtime/server`, and
  `next-config` exports.

### Changed

* Next.js production scaffolds must use exactly `15.5.19`, the version covered
  by the relocation manifest and canary.

## [0.4.3] — 2026-07-27 (ENG-9199)

### Fixed

* The Next.js proxy and local-development auth bridge now share the complete
  signed ForwardAuth envelope introduced by the platform AuthZ rollout,
  including groups, attributes hash, authorized party, and stable signature.
  This prevents the default frontend-to-backend proxy from stripping fields
  before Python's guarded `platform_request()` forwards the request to a
  platform API.

## [0.4.2] — 2026-07-16 (ENG-8753)

### Fixed

* **Ships the Next.js 15 `RouteHandler` fix to npm.** The ENG-1734 migration
  (PR #191, merged 2026-06-22) rewrote the proxy `RouteHandler` type so
  `createProxyHandlers()` handlers type-check under Next 15's build-time
  route validation (Next 15 made dynamic-route `params` a `Promise`; the
  published type still declared the Next-14 sync `Record` shape). PR #191
  landed on `main` without a version bump, so the 2026-07-13 release could
  not publish it over the existing `0.4.1` — every fresh
  `kz-ext create -t app` scaffold (which installs `next ^15` and this lib
  from npm) failed `next build` at container startup and crash-looped.
  This release publishes that fix; no code changes beyond ENG-1734's.

## [0.4.1] — 2026-06-14 (ENG-6911)

### Fixed

* **`SessionProvider.logout()` now navigates to the platform front-channel
  logout URL.** It previously read only `redirect_url` (→ `/logged-out`)
  and never visited core's front-channel GET — the only endpoint that
  clears the auth-gateway / Keycloak SSO cookies and ends the SSO session.
  Extensions using the default `SessionProvider` therefore "logged out"
  but silently re-authenticated on the next visit (same root cause as the
  Workroom Manager bug fixed in the Python `kamiwaza-extensions-lib`
  0.4.1). `logout()` now prefers `front_channel_logout_url` from the
  logout response when present, falling back to the existing
  `redirect_url` → logged-out behavior.
* New exported helper `isTrustedFrontChannelUrl(url)` validates the
  backend-provided front-channel URL. Unlike `isSafeRedirect`, it permits
  a different origin than the app (the platform API host may differ from
  the app host — e.g. split origins under `kz-ext dev local --auth`),
  since the URL is produced by the extension's own backend and core
  validates the embedded `redirect_uri` against its allowed hosts. It
  still rejects non-http(s) schemes (`javascript:`, `data:`) and
  protocol-relative URLs.

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
