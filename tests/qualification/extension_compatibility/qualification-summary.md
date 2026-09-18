# Installed qualification: 2026-09-16 UTC

> Historical evidence for the original automatic-selection implementation.
> Superseded by the explicit administrator-controlled selection requirements in
> [Core PR #2842](https://github.com/kamiwaza-internal/kamiwaza/pull/2842).
> The receipts below are preserved unchanged; they do not prove the newer
> availability-only refresh, explicit update, or selection rollback behavior.

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
- SDK final-candidate publication rerun: `515807b724a5cc1c581338abdab199d48a278531`,
  including legacy grammar fix `69a7ede` and the boto3 floor change. All 74 copied
  Python source files were SHA-256 verified before execution. See
  [final SDK source hashes](receipts/final-sdk/source-hashes.json).
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

## Final SDK candidate rerun

The final SDK candidate repeated baseline and update publication against a second,
independent loopback S3 emulator on port 18334, with its own source copy and export
directory. It did not touch the working instance catalog or the original S3
server. Both stale conditional-write probes returned 412; four concurrent
publishers again retained all four releases. Baseline and updated object bytes,
ETags, writer metadata, and release histories exactly match the objects consumed
by the installed matrix. This closes the source-version gap between the initial
live publication and the final SDK fixes.

[Final baseline](receipts/final-sdk/catalog-baseline.json),
[final update](receipts/final-sdk/catalog-update.json), and
[dependency versions](receipts/final-sdk/runtime-dependencies.json) preserve this
rerun. boto3 and botocore were both 1.43.95; Moto was 5.1.12.

## Final runtime and submission evidence

The original three fresh-install matrix above retains its original source
provenance. Subsequent runtime fixes were additionally verified on the retained
Core 1.4.0 instance; this does not represent another complete three-install run.

- Core `c145ee6753c3e4511917e31be64a0df2f6160c03` and Kajiya
  `3e9669b5fc47e35de885d9586a9550146780de95` were copied and hash-verified, then
  exercised against the installed instance. [Exact source hashes](receipts/final-runtime/source-hashes.json).
- The [final Core API receipt](receipts/final-runtime/instance-1.4.0.json) confirms
  canonical 1.4.0, selected/persisted 0.6.0, future-only exclusion, and stable
  repeated sync. The [final guard receipt](receipts/final-runtime/incompatible-deployment-1.4.0.json)
  proves an incompatible direct deployment receives actionable HTTP 400, leaves
  deployment identities unchanged, and removes the negative test template.
- All six original 0.5.0 and new 0.6.0 app/service/tool workloads still returned
  their respective versions. [Workload responses](receipts/final-runtime/workloads.json)
  and [pod readiness/image identities](receipts/final-runtime/pods.json).
- The [final Kajiya import receipt](receipts/final-runtime/kajiya-import-1.4.0.json)
  confirms both actual imports preserved the selected versions and template IDs.
  Later Core `4b0e2d8` and Kajiya `320bb0dc` changes are corpus metadata changes,
  not runtime changes to the source exercised here.
- SDK submission `d21a64ab08224e41d901c05a85274d3b98135637` additionally passed 110
  focused publishing/preflight tests. Those preflight changes are distinguished
  from the actual publisher rerun at `515807b` above, whose resulting bytes match
  the installed proof exactly.
- Shared workflow `3e1df34` passed 232 tests and shell checks. Deploy `62f2cf8`
  includes the `eab167` runtime refactor and the actual six snapshot projections
  in [deploy-projections.json](receipts/final-runtime/deploy-projections.json).
  The app/tool input hashes match the SDK exports. The first attempt correctly
  rejected the harness's empty connectors array; the successful projections use
  a separate minimal valid v3 connector fixture, preserving the app/tool bytes.
  All six build and `--check` commands exited zero.
- An actual untouched pre-feature Core reader at
  `abcd7548d707d00dd0aaff18065881ffacd3837b` retained v3 behavior while compat-v1
  files changed and its cache was cleared. Eight observed catalog opens were all
  under `garden/v3`; v3 hashes and results stayed unchanged. This used the real
  file-URI reader without transport mocks. [Legacy reader receipt](receipts/final-runtime/legacy-reader.json).
  This is library compatibility proof, not a full installation of an old release.

Final receipts omit authentication bootstrap output and retain only workload
response arrays from the runtime log. No private token files or authorization
headers were copied.

## Explicit-selection successor qualification

The current admin-controlled selection implementation is qualified by the [isolated instance receipt](https://github.com/kamiwaza-internal/kamiwaza/blob/3604f3616c/tests/qualification/extension_selection/receipt.json) and [operator qualification narrative](https://github.com/kamiwaza-ai/kamiwaza-docs/pull/243). It separately records the full straw-version matrix and final-source targeted checks; this historical report remains unchanged evidence of the earlier behavior.
