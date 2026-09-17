# SDK changelog

## 1.2.0 — 2026-09-17

### Breaking coordinated release

Deploy with the matching generic-markings Core 1.3.0 or later and both extension
runtime libraries on 0.6.x. This intentionally replaces earlier marking request
fields and trusted identity fields; update callers and qualify the complete
producer/consumer set before rollout. Versions are coordinated release
coordinates, not evidence that an arbitrary build has the required contract.

- Resource create/update calls accept a profile-qualified `marking` envelope:
  `profile_id`, `profile_revision`, `level_id`, `raw_text`, and provider-owned
  `attributes`. Resource response `level_id` is derived, not an assignment API.
- Connector configuration uses `level_boundary` and `default_marking`; document
  indexing uses `marking_text` for server normalization. Stale request fields
  are rejected by strict SDK models or the paired server, not silently migrated.
- Query `client.markings.config()` before showing configured selectors; use
  `parse()` to normalize text and `compose()` for presentation. The SDK embeds
  no vocabulary or parser and does not infer authorization from display text.
- Omitted update fields preserve the existing value. Explicit null asks to clear
  it, subject to server validation and authorization. No SDK default is assigned.
- Trusted identity now uses `marking_level` in Python/JSON and `markingLevel` in
  TypeScript, transported by `X-User-Marking-Level`. The signed payload changes;
  upgrade verified producers and consumers together. Do not copy untrusted
  browser headers into the authenticated envelope.
- The shared-IdP validation provider revision becomes
  `sdk.federation.shared-idp@v2` because its case IDs and fixture personas changed.
  Scenario and protocol schemas remain v1. Clean old owned fixtures with the
  matching previous provider, then regenerate plans/state for the new provider.
  New code refuses old state rather than adopting its ownership.

The server feature is disabled by default. Provider installation alone does not
activate it. Offline media must include the selected provider and dependencies
for every runtime architecture. This release targets clean installations;
existing marked databases need explicit conversion or a backed-up reinstall.
No automatic data migration, registry publication, or deployment is implied.

See [the markings API guide](docs/services/markings/README.md) and
[validation lifecycle guide](docs/validation/federation-lifecycle.md).
