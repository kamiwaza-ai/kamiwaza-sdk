# Changelog — `kamiwaza-extensions-lib` (Python runtime)

All notable changes to this runtime library are documented here. Versions
follow semver. The library is distributed alongside `kamiwaza-sdk` but
versioned independently — extension authors pin against the `[lib]` minor
range in `requirements.txt`.

## [0.4.0] — 2026-04-30 (D210 M3 — PR #87)

### Added

* **`kamiwaza_extensions_lib.url`** — public URL-resolution helpers
  (`public_base_url`, `backend_runtime_base`) for scaffolded extensions
  to import without coupling to a private path. Replaces the round-8
  internal `_url` module (the underscored module is gone — the renamed
  public form is the single source of truth). The helpers are
  re-exported from the package root so callers write
  `from kamiwaza_extensions_lib import backend_runtime_base`. Round-9
  review caught the template importing the underscored path; promoting
  the module is the durable fix.
* **`kamiwaza_extensions_lib.local_dev`** — opt-in local-auth bridge for
  `kz-ext dev local --auth`. Provides `prepare_bridge_context`,
  `BridgeContext`, `LocalDevAuthError`, plus the `host.docker.internal`
  rewrite + browser-vs-container URL split. The runtime CLI overlay is
  the public consumer; extension authors do not call this module
  directly.

### Fixed

* `_strip_api_suffix` now produces identical output for `…/api` and
  `…/api/` — round-9 review caught a divergence between this helper and
  the trailing-slash handling in the (now-deleted)
  `local_dev.public_api_url_from` helper.
* The `kz-ext dev local --auth` env overlay no longer strips `/api`
  from `KAMIWAZA_PUBLIC_API_URL`. `session.create_session_router`
  builds `${base}/auth/login` directly and the platform serves auth
  endpoints under `/api/auth/*`, so stripping here produced 404
  redirects on every login under `--auth`. Round-10 codex P2.
* `_default_resolver` switched from `socket.gethostbyname` (IPv4-only)
  to `socket.getaddrinfo` (dual-stack). AAAA-only Kamiwaza hostnames
  previously raised `gaierror` here, causing `is_loopback_url` to
  treat them as "unresolvable" and `build_compose_extra_hosts` to
  silently route platform traffic to the developer's machine via
  `host-gateway`. Round-11 codex GH High.
* `is_loopback_url` now checks the resolved IP for loopback-range
  membership instead of treating any successful resolution as
  "non-loopback". A developer with `kamiwaza.dev` mapped to
  `127.0.0.1` in `/etc/hosts` was previously skipped by
  `build_compose_extra_hosts`, so the container couldn't reach the
  alias. Round-12 codex P2.
* `_resolve_openai_base` now re-hosts a deployment's ``endpoint``
  field onto the container-routable base when the platform emits a
  browser-only host (``localhost``, ``host.docker.internal`` from a
  different container). Without this, ``get_model_client()`` would
  configure AsyncOpenAI with a URL the backend container can't reach.
  ``list_available_models`` (frontend display) is unaffected — its
  endpoint values remain verbatim so the UI matches the platform's
  reported URLs. Round-12 codex P2.
* `is_loopback_url` no longer treats DNS-resolution failures as
  loopback. A transient resolver failure (VPN drop, captive portal)
  on a corp hostname previously caused
  `build_compose_extra_hosts` to map the hostname to ``host-gateway``,
  which under ``--auth`` could route the forwarded bearer to whatever
  was listening on the developer's loopback. Now the request fails
  loudly inside the container with a DNS error, surfacing the actual
  cause. Legitimate ``/etc/hosts``-aliased loopbacks (the path the
  prior fallback was trying to handle) resolve correctly via
  ``getaddrinfo`` and are caught by the round-12 resolved-IP check
  above. Round-12 GH PR review codex H2.
* `_is_loopback_ip` also detects IPv4-mapped IPv6 forms
  (``::ffff:127.0.0.1``) as loopback, so an AAAA-leading resolver
  answer for an aliased loopback is classified correctly. Round-12
  Claude M.

### Internal

* Consolidated the signature-less JWT payload decoder into
  `kamiwaza_extensions_lib._jwt`. Both `session._decode_jwt_exp` and
  `local_dev._decode_jwt_claims` now delegate; the prior implementations
  diverged on segment-count strictness (round-10 review). Round-11
  also collapsed `local_dev._coerce_int` onto `_jwt.decode_jwt_exp`
  so the NumericDate coercion is no longer duplicated.
* Removed `local_dev.public_api_url_from` — its only caller (the env
  overlay) no longer strips `/api`, and the public `url._strip_api_suffix`
  covers the remaining browser-display path. Single source of truth
  for `/api`-stripping (round-10 review).

### Notes

* Compat floor in `kamiwaza_extensions/compatibility.json` raised to
  `>=0.4,<0.5` so pip cannot resolve a 0.3.x version that lacks the
  public `url` module against scaffolded extensions.

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
