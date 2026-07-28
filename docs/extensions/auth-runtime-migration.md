# Auth Runtime Migration

The canonical auth and runtime contract lives in
`@kamiwaza-ai/extensions-lib@0.5.x` and
`kamiwaza-extensions-lib==0.5.*`.

The deprecated `kamiwaza-extensions-template` repository's
`@kamiwaza/auth@0.2.0` and `kamiwaza_auth` packages are legacy
implementations. They are not re-export layers: their cookie-based path
behavior is incompatible with the bootstrap-derived runtime path used by 0.5.

## Import map

| Legacy import | Canonical import |
|---|---|
| `@kamiwaza/auth/client` session components | `@kamiwaza-ai/extensions-lib/client` |
| `@kamiwaza/auth/server` proxy helpers | `@kamiwaza-ai/extensions-lib/server` |
| `@kamiwaza/auth/middleware` | `@kamiwaza-ai/extensions-lib/local-dev-auth` |
| `@kamiwaza/auth` `apiFetch` | `@kamiwaza-ai/extensions-lib/runtime` `appFetch` |
| `@kamiwaza/auth` base-path helpers | `@kamiwaza-ai/extensions-lib/runtime` |
| `kamiwaza_auth` | `kamiwaza_extensions_lib` |
| `kamiwaza_auth.endpoints.create_session_router` | `kamiwaza_extensions_lib.create_session_router` |

These are conceptual mappings, not guaranteed signature-compatible aliases.
Update each call site to the canonical API shown in the
[Auth Integration Guide](./auth-integration-guide.md).

## Staged policy

1. New extensions use only the canonical packages.
2. Existing extensions may remain on the frozen legacy packages until they
   are deliberately migrated.
3. Do not mix legacy and canonical auth/path helpers in one frontend.
4. Remove legacy package artifacts from an extension only after its imports
   and lockfiles have been migrated.

The SDK's 0.5 runtime packages are the release gate for clean dependency
resolution and lockfile regeneration. The SDK-owned runtime canaries exercise
local release artifacts before publication.
