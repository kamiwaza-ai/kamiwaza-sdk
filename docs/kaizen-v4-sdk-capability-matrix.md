# Kaizen v4 SDK capability audit

Status: **cutover no-go**

Audit ticket: [ENG-10507](https://linear.app/kamiwaza/issue/ENG-10507)
Audited: 2026-08-17

## Decision

Kaizen v4 has a sound request-scoped SDK integration for model, deployment,
extension, dataset, workroom, authentication, authorization, tenant-scope, and
Context operations. It does not yet meet the full supported-SDK cutover bar:

1. Skills Library SDK contracts exist, but the default Kaizen agent does not
   register them. The generic OpenAPI Skills tag is deployment-policy opt-in.
   [ENG-9983](https://linear.app/kamiwaza/issue/ENG-9983) owns Kaizen v4
   consumption qualification.
2. Connector instance administration is in the SDK. The connector-surface
   catalog, verification, browse/search, and content-fetch contracts have since
   been added to `ConnectorService`, so Kaizen no longer *needs* direct `httpx`
   adapters for that runtime path — but the Kaizen-side migration onto them has
   not landed yet, and the fresh-deployment runtime proof remains outstanding.
   [ENG-10518](https://linear.app/kamiwaza/issue/ENG-10518) owns closure.
3. Fresh-deployment runtime coverage remains incomplete outside the now-proven
   Context path; the original Core scheduler readiness failure remains tracked by
   [ENG-10492](https://linear.app/kamiwaza/issue/ENG-10492).

No other missing SDK contract was confirmed in the audited product classes.
The cutover decision should be revisited after the three items above have current
runtime evidence.

## Audit basis

The audit compares the SDK contract, the exact SDK revision consumed by Tomo,
the Kaizen v4 runtime registration, focused contract tests, and a local live
rollout observation.

| Component | Revision audited | Notes |
| --- | --- | --- |
| `kamiwaza-sdk` `develop` | `238c913fc43014e622bc20f24d4e55abc7a34ded` | Audit artifact base. Package version is `1.1.0`. |
| Tomo/Kaizen v4 `develop` | `f2b12889a9ab246993d63ea5b90a5198ff1592d2` | Pins both SDK distributions to SDK commit `6be36aff7ccba2e5c80799852fd2a29bd56946a9`. |
| Tomo-pinned SDK | `6be36aff7ccba2e5c80799852fd2a29bd56946a9` | Capability client/service files match the audited `develop` base; only delegated-workload readiness code differs, outside this matrix. |
| Core `develop` live image source | `c8b6c9af03a1b16cdfb0ac019f7b41cf4110d861` | Scheduler is blocked by ENG-10492. |
| Deploy `develop` | `b52edebd20170ad72f4de5099810d276ca0aca73` | Local k0s/Lima deployment path used for the rollout attempt. |

Classification vocabulary:

- **Supported** — required SDK contract is registered in Kaizen v4.
- **Missing SDK contract** — required Core route family has no typed SDK surface.
- **SDK exists, not exposed** — the SDK method exists but is not registered on
  the ordinary Kaizen v4 path.
- **Policy/prerequisite** — the route is intentionally conditional on identity,
  workroom, deployment policy, or an installed/configured resource.
- **Deliberately out of scope** — the operation should remain outside the
  ordinary agent surface.

## Capability-kit reconciliation

This matrix was reconciled on 2026-08-18 with the current
[`capability-kit`](https://github.com/kamiwaza-internal/capability-kit) corpus.
That corpus is the evidence vocabulary and capability record layer, not a
replacement for this implementation matrix. The two artifacts therefore join
at capability and evidence identifiers but retain deliberately non-equivalent
fields:

| This audit | Capability-kit counterpart | Reconciliation |
| --- | --- | --- |
| Capability, supported SDK entry point | `id`, `title`, `surface`, `requires`, `preconditions` in `capability.v1` | A kit capability may span several SDK methods; the audit retains method-level entries so a missing or unregistered method cannot be hidden by an aggregate product label. |
| Kaizen v4 runtime registration | None | This is Tomo-specific implementation evidence. It remains an audit-only field because the kit deliberately does not infer extension wiring from a platform SDK contract. |
| Classification and owner / closure | None | These are engineering triage decisions, not customer-capability claims. |
| Representative verification | `evidence_plan`, `evidence`, and a `scenario-evidence.v2` record | Existing test counts prove contract coverage but are not fresh-deployment evidence. A closing record must name the executed build, method, capability IDs, status, and ordered observations. |

### Context mapping and closure preconditions

The kit's `context.workroom-document-search` capability maps directly to the
three Context rows in this matrix: collections, ingest/pipeline lifecycle, and
retrieval. Its preconditions make the default-path closure more precise:

- Every call must remain under the authorized current workroom; a cross-workroom
  job or collection must not become visible through the default agent path.
- Managed vector-store and embedding/parser dependencies are prerequisites. A
  missing dependency must surface the Context service's specific, actionable
  error rather than a fallback to an unrelated Kaizen tool.
- Context writes must target an explicit non-Global workroom. The SDK documents
  the Global Workroom as read-only: collection, ingest, and ontology writes
  return 403 there, while reads remain allowed.

The corpus has **no separate ontology capability record** today. That is a
documentation/evidence gap, not a missing SDK contract: the SDK Context service
already supplies ontology CRUD, knowledge/entity writes, and queries. Before
ENG-10505 can emit its required end-to-end evidence, capability-kit needs an
ontology capability identifier or an explicitly reviewed decision to extend
`context.workroom-document-search`; the evidence schema requires every emitted
`capability_ids[]` value to join to a live document.

The `kaizen.data-chat` record is intentionally not evidence that Context is
registered by default: it declares that Kaizen extension internals were outside
its source scope. Its `UI` evidence plan can cover a conversation experience
only after the Context default-registration proof exists; it cannot replace the
SDK-backed ingestion/retrieval or ontology round trips required here.

## Capability matrix

| Capability | Supported SDK entry point | Kaizen v4 runtime registration | Classification | Representative verification | Owner / closure |
| --- | --- | --- | --- | --- | --- |
| Model discovery and inspection | `client.models.list_models`, `search_models`, `get_model_configs`, and model-file reads | `kamiwaza_extensions_lib.list_available_models`; direct `inspect_kamiwaza` operations `list_models`, `get_model`, `list_model_configs` | Supported | SDK model and extensions-lib model tests; Tomo closed-tool tests | SDK + Tomo |
| Model download | `client.models.initiate_model_download` | `manage_kamiwaza_demo.ensure_model_download` with preview/apply and bounded projections | Supported; apply is approval-gated | SDK model tests; Tomo direct-SDK and staging tests | SDK + Tomo |
| Model deployment and inference | `client.serving.deploy_model`, deployment list/get/status/wait; OpenAI-compatible client | `manage_kamiwaza_demo.ensure_model_deployment`; `inspect_kamiwaza` deployment reads; extensions-lib model client for inference | Supported; a ready model/runtime is a prerequisite | SDK serving tests; Tomo SDK-tool and platform-model tests | SDK + Tomo; fresh proof blocked by ENG-10492 |
| Connector instance administration | `client.connectors.list`, `list_available`, `get`, `create`, `update`, `register_type`, `subscribe`, `delete` | Read-only `inspect_kamiwaza.list_connectors`; generic OpenAPI connector writes only when selected by deployment policy | Supported for inspection; writes are policy/prerequisite | SDK connector tests; Tomo capability-catalog and OpenAPI tests | SDK + Tomo |
| Connector surface discovery and use | `client.connectors.list_surface_catalog`, `verify_connection`, `browse_surface`, `search_surface`, `fetch_surface_content` | Tomo connector catalog, verification, per-surface tools, and federated connector RAG still call Core directly with request-scoped `httpx`; they are ready to migrate onto the SDK surface methods | SDK contract landed; **migration not yet landed, runtime proof pending** | SDK connector-surface unit tests cover success, authorization failure, not-ready/unsupported states, pagination, and binary/text content; Tomo connector tests prove envelope, bounds, and fail-closed behavior | [ENG-10518](https://linear.app/kamiwaza/issue/ENG-10518) |
| Connector provider OAuth and secrets | SDK exposes admin connector contracts, but not an interactive provider-login ceremony | Provider authorization remains a Core/UI flow; Tomo receives only opaque handles and forwarded identity | Deliberately out of scope for the agent; connected provider is a prerequisite | Connector tests confirm provider credentials are not accepted or persisted by Tomo | Core connector UI + policy |
| Dataset lifecycle and attribute gates | `client.datasets.create`, `get`, `delete`, `set_gate`, `get_gate`, `clear_gate`; catalog dataset listing | `inspect_kamiwaza` operations `list_datasets` and `get_dataset`; `manage_kamiwaza_demo.ensure_dataset` for bounded create/reuse | Supported; writes are approval-gated | SDK dataset/catalog tests and Tomo direct-SDK tests | SDK + Tomo |
| Skills Library catalog/package lifecycle | `client.skills.list_skills`, `get_skill`, `import_skill_package`, `download_skill_package`, `export_skill_package`, `export_skills_bundle`, `update_skill_metadata`, `delete_skill` | No direct SDK tool on the default agent; `openapi_tag:skills` can be enabled through deployment policy; local agent bundles are a separate Kaizen package system | **SDK exists, not exposed** on the default path | SDK Skills Library tests pass; Tomo OpenAPI policy tests prove opt-in discovery but not default consumption | [ENG-9983](https://linear.app/kamiwaza/issue/ENG-9983) |
| Context collections | `client.context.list_collections`, `create_collection`, `get_collection`, `delete_collection` | Default closed Context SDK tool family in Tomo; specialized bundles remain optional | Supported; writes retain confirmation and audit gates | PR #243 tests; default registration is no longer policy-selected | [ENG-10505](https://linear.app/kamiwaza/issue/ENG-10505), merged |
| Context ingest and pipeline lifecycle | `client.context.upload_file`, raw-file create/list/get/update, pipeline/import options, source import, item replay/retry/rerun/cancel, OmniParse lifecycle | Default closed Context SDK tool family in Tomo; specialized bundles remain optional | Supported; storage/parser/embedding resources are prerequisites and writes retain confirmation/audit gates | PR #243 tests; default registration is no longer policy-selected | [ENG-10505](https://linear.app/kamiwaza/issue/ENG-10505), merged |
| Context retrieval | `client.context.search`, `retrieve`, `agentic_search`, vector queries | Default `search_knowledge` Context SDK tool with required query/group schema fields | Supported | PR #246 tests; `search_knowledge` in group `eng10505-validation` retrieved `amber-cypress-917` | [ENG-10505](https://linear.app/kamiwaza/issue/ENG-10505), merged |
| Context ontology | `client.context` ontology list/get/create/delete, add knowledge/entity, search, memory, episodes, group delete, health | Default `add_knowledge` Context SDK tool; mutation is approval-gated | Supported; unrestricted ontology mutation is deliberately not implicit | Fresh Kaizen-chat approved `add_knowledge` wrote `amber-cypress-917`; PR #246 tests | [ENG-10505](https://linear.app/kamiwaza/issue/ENG-10505), merged |
| Authentication | `client.auth` password login/refresh/logout/current-user/forward-auth; extensions-lib identity helpers | Tomo validates the signed request identity and constructs a `KamiwazaClient` with a closed forwarded-envelope authenticator | Supported; identity-provider and user administration are deliberately outside the ordinary agent | SDK auth and extensions-lib auth tests; Tomo tool tests verify exact header forwarding | SDK + Tomo |
| Authorization | `client.authz` tuple upsert/delete/object delete/access check | Core remains final authority; Tomo tools carry the validated caller envelope and classify actor-private/bound operations; mutations retain confirmation gates | Supported request boundary; authorization administration is deliberately out of scope | Static SDK service inspection plus Tomo SDK/connector policy tests | SDK + Core + Tomo |
| Tenant and workroom scope | `client.workrooms` CRUD/enter/leave/export/ingestion summary; client workroom-scope recovery | `_platform_context` binds API base, verified forwarded headers, and current workroom; SDK projections never accept caller-authored endpoint or credential fields | Supported; membership is a prerequisite | SDK workroom/scope tests and Tomo SDK-tool tests | SDK + Core + Tomo |
| App, extension, and runtime discovery | `client.apps`, `client.extensions`, `client.serving`, and model deployment reads | `inspect_kamiwaza` exposes app deployments, extensions/status, deployments, models, datasets, and workrooms; `manage_kamiwaza_demo` exposes bounded ensure operations | Supported; install/deploy writes are approval-gated | SDK serving/app/extension tests and Tomo direct-SDK tests | SDK + Tomo; fresh proof blocked by ENG-10492 |

## Verification evidence

### Static and contract evidence

- SDK focused service suite: **324 passed** from the Context, Skills Library,
  connectors, auth, serving, workrooms, models, workroom-scope recovery, and
  extensions-lib identity/model test files. A second app/extension command
  passed **22 tests** (`test_extension_service.py` and
  `test_apps_install_by_name.py`).
- Tomo focused suite: **73 passed** from the closed SDK tool, SDK runtime-grant,
  OpenAPI broker/runtime, connector-surface, platform-model inventory, and
  member capability-catalog test files.
- The tests establish typed contracts, runtime registration, policy
  intersection, identity forwarding, bounded projections, and negative/failure
  behavior. They are not substitutes for fresh-deployment evidence.

### Live rollout attempt

The local k0s/Lima cluster was started and its persisted `develop` workloads
were observed. Frontend and extension-operator deployments became ready, but
`core-scheduler` remained in `CrashLoopBackOff`. Its compiled Core process
exited before scheduler readiness with:

```text
AttributeError: Can't get local object '_add_readiness_route.<locals>.held_capabilities'
```

That exact compiled-image/Ray cloudpickle regression is tracked by
[ENG-10492](https://linear.app/kamiwaza/issue/ENG-10492) and its open Core PR.
Because Core never became ready, no capability class received the required
fresh-deployment runtime proof. This audit does not relabel the persisted-cluster
startup observation or mocked tests as fresh evidence.

When ENG-10492 lands and a rebuilt image is available, rerun at least one
ordinary-member proof for every supported class above, including negative
tenant/workroom cases. Context, Skills Library, and connector-surface proofs
must run only after their respective closure tickets land.

### Context closure evidence (2026-08-19)

[ENG-10505](https://linear.app/kamiwaza/issue/ENG-10505) closed the default
Context runtime-registration gap through Tomo PR
[#243](https://github.com/kamiwaza-internal/kamiwaza-extensions-tomo/pull/243)
and schema/approval hardening in PR
[#246](https://github.com/kamiwaza-internal/kamiwaza-extensions-tomo/pull/246),
merged as `3d246a1b7a35f53c5f38ebdfff6972d282ab484b`. PR #246 passed 42
checks and had current-head approvals from Kevin and `kamiwaza-pr-verify`.

In a fresh Kaizen chat, an approved `add_knowledge` call wrote
`amber-cypress-917`; `search_knowledge` scoped to group
`eng10505-validation` retrieved that exact token. This is a Context-specific
runtime proof. It removes Context from the cutover blockers; it does not claim
connector-surface coverage or substitute for the remaining fresh-deployment
proofs.

## Follow-up ownership

- Context default-path findings were closed by
  [ENG-10505](https://linear.app/kamiwaza/issue/ENG-10505).
- Skills Library consumption remains with
  [ENG-9983](https://linear.app/kamiwaza/issue/ENG-9983).
- The new connector SDK contract is
  [ENG-10518](https://linear.app/kamiwaza/issue/ENG-10518), seeded in the SDK
  Maestro ledger with a cross-repo Tomo reference.
- The non-blocking schema cross-check requested from Jonathan is tracked by
  [ENG-10519](https://linear.app/kamiwaza/issue/ENG-10519).
- The runtime blocker remains
  [ENG-10492](https://linear.app/kamiwaza/issue/ENG-10492); it is not duplicated.
