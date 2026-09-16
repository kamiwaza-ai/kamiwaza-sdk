# Installed qualification: 2026-09-16 UTC

All three installed straw-version cases passed against a full, working isolated
Kamiwaza instance. These are compatibility algorithm and deployment proofs, not
certification of future released Core builds.

| Canonical running Core | Baseline selected/persisted/deployed | After publishing extension 0.6.0 requiring >=1.3.1 | Running workloads |
|---|---|---|---|
| 1.3.0 | 0.3.0 | 0.3.0 | Existing 0.3.0 app remains unchanged |
| 1.3.1 | 0.4.0 | 0.6.0 | All three old 0.4.0 workloads coexist with three new 0.6.0 workloads |
| 1.4.0 | 0.5.0 | 0.6.0 | All three old 0.5.0 workloads coexist with three new 0.6.0 workloads |

Each selection column covers **app, service, and tool**. Baseline publication also
contains extension 0.2.0 requiring >=1.2.1. Future-only entries requiring >=9.0.0
are absent from remote selected lists and persisted templates in every case.
Actual HTTP responses identify the workload name and extension version. Apps and
tools use the ingress route; services use their internal cluster endpoint because
the existing service NetworkPolicy intentionally denies external ingress.

## Environment and source identity

- One task-owned Firestorm KVM guest, 8 vCPU / 48 GiB, native k0s. Each canonical
  case received a fresh tenant install, including new Core/Postgres/SpiceDB/Keycloak
  state; this was not a mocked API or manual template/database version rewrite.
- Full authentication and ReBAC enabled. `core.license.enforce=false` was an
  explicit qualification setting; production license enforcement was not tested.
- The canonical mounted version file was changed to each straw Core version.
  Scheduler and Ray probes plus authenticated `/api/apps/remote/compatibility`
  confirmed the actual version used by selection.
- Core runtime source matches commit `8eba43a138c8a6fac21deaee7e5ff62e3ac40458`;
  all eight changed runtime files were hash-verified in the guest. See
  [source hashes](receipts/core-source-hashes.json). Earlier commit `6bee46a`
  has the same runtime files; subsequent changes were docs/generated OpenAPI.
- SDK publishing implementation: `e209839` plus the committed qualification
  harness in this directory. Core selection and publisher were executed directly.
- Kajiya importer: `36d778495c660f5cb654369dacc13ae105d875a5`.
- Deploy candidate: `d12fe53dd1ead35922ef6c7306d7e607443b1281`.
- Shared publishing CI candidate: `37800d95f7f4a7fe9ae9f5b2fb051e2ccae43715`;
  live production CI publication was not run.

The task used a loopback-only Moto 5.1.12 HTTP S3 protocol emulator and fixed dummy
credentials. No saved cloud profile or production Cloudflare bucket was used.
The real `RegistryBuilder` and `CatalogPublisher` generated and published objects.
Exact object bytes were exported to the guest's catalog directory, then exposed
through the instance's trusted HTTPS ingress. Core's HTTP prohibition and TLS
verification were preserved. SHA-256 and S3 ETag receipts identify the bytes.

## What was proved

- Baseline and later release history survives, with opaque metadata and writer
  capability metadata retained. Real S3 HTTP stale `IfMatch` and existing-object
  `IfNoneMatch` writes both returned 412. Four independent concurrent SDK
  publishers retained all four releases. This qualifies the emulator protocol
  behavior; it does not claim production R2 implementation certification.
- Every installed case positively asserted canonical runtime version before
  checking selected remote catalogs, actual sync, and actual persisted templates.
  Repeated sync preserves IDs and content. Future-only declarations are ignored.
- The 0.6.0 release was published once through the SDK. Later fresh cases replayed
  the exact baseline and updated object bytes; they did not claim new publication.
- Bulk sync advances stored templates while preserving their IDs and existing
  deployment references. Existing workloads continue their old compose/version;
  newly requested deployments use 0.6.0. Bulk sync does not freeze the stored
  template at the deployed version.
- An incompatible direct template requiring >=9.0.0 was created through the real
  API, then deployment returned 400. The server log identifies the compatibility
  guard as the cause, deployment IDs were unchanged, and the exact negative
  template was removed. See [receipt](receipts/deployment-guard-1.3.0.json) and
  [guard log](receipts/incompatible-deployment-log.txt).
- The actual Kajiya direct importer ran twice per installed Core version against
  exported catalog bytes. All six runs exited zero, preserved IDs and selected
  versions, and excluded future-only entries. It did not invoke deployment APIs.

## Reproduce and inspect

[README](README.md) describes isolated storage setup and commands.
`catalog_harness.py` publishes/exports fixtures, `instance_harness.py` asserts the
installed selection matrix, `deployment_guard.py` verifies incompatible refusal,
and `active_update_guard.py` verifies template/deployment identity preservation.
Deploy the selected templates using the real app/tool APIs and query their HTTP
endpoints; the fixture responds with JSON on port 8080. Preserve both old and new
workloads during an update to prove coexistence.

The [receipts](receipts/) directory contains sanitized API/S3 results, workload
responses, and source hashes. `instance-baseline-*` and `instance-update-*` cover
all six selection points. `runtime-baseline-after-update-*` and `runtime-update-*`
show old/new workload coexistence. The 1.3.0 browser/internal responses are in
[runtime-1.3.0-browser.txt](receipts/runtime-1.3.0-browser.txt). No credentials,
private tokens, saved cloud profiles, or unrelated installation logs are included.
