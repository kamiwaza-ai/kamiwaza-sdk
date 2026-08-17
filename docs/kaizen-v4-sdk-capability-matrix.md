# Kaizen v4 SDK capability audit

Status: **cutover no-go**  
Audit ticket: [ENG-10507](https://linear.app/kamiwaza/issue/ENG-10507)  
Audited: 2026-08-17

## Decision

Kaizen v4 has a sound request-scoped SDK integration for model, deployment,
extension, dataset, workroom, authentication, authorization, and tenant-scope
operations. It does not yet meet the full supported-SDK cutover bar:

1. Context SDK contracts exist, but collections, ingest/retrieval, and ontology
   operations are not available to the default Kaizen agent. They require an
   administrator-selected generic OpenAPI surface or a specialized bundle.
   [ENG-10505](https://linear.app/kamiwaza/issue/ENG-10505) owns closure.
2. Skills Library SDK contracts exist, but the default Kaizen agent does not
   register them. The generic OpenAPI Skills tag is deployment-policy opt-in.
   [ENG-9983](https://linear.app/kamiwaza/issue/ENG-9983) owns Kaizen v4
   consumption qualification.
3. Connector instance administration is in the SDK, but the connector-surface
   catalog, verification, browse/search, and content-fetch contracts are not.
   Kaizen therefore owns direct `httpx` adapters for a required runtime path.
   [ENG-10518](https://linear.app/kamiwaza/issue/ENG-10518) owns closure.
4. The required fresh-deployment runtime proofs cannot currently run because
   the compiled Core scheduler crashloops before readiness.
   [ENG-10492](https://linear.app/kamiwaza/issue/ENG-10492) owns that blocker.

No other missing SDK contract was confirmed in the audited product classes.
The cutover decision should be revisited after the four items above have current
runtime evidence.

## Audit basis

The audit compares the SDK contract, the exact SDK revision consumed by Tomo,
the Kaizen v4 runtime registration, focused contract tests, and a local live
rollout observation.

| Component | Revision audited | Notes |
| --- | --- | --- |
| `kamiwaza-sdk` `release/1.2.0` | `afcc3ec6b8011b0c0d32a51eaeab1b86175dc805` | Audit artifact base. Package version is `1.1.0`. |
| Tomo/Kaizen v4 `develop` | `f2b12889a9ab246993d63ea5b90a5198ff1592d2` | Pins both SDK distributions to SDK commit `6be36aff7ccba2e5c80799852fd2a29bd56946a9`. |
| Tomo-pinned SDK | `6be36aff7ccba2e5c80799852fd2a29bd56946a9` | Relevant client/service files match the audited release branch; the develop-only delegated-workload package is outside this matrix. |
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

## Capability matrix

| Capability | Supported SDK entry point | Kaizen v4 runtime registration | Classification | Representative verification | Owner / closure |
| --- | --- | --- | --- | --- | --- |
| Model discovery and inspection | `client.models.list_models`, `search_models`, model/config/file reads | `kamiwaza_extensions_lib.list_available_models`; direct `inspect_kamiwaza` operations `list_models`, `get_model`, `list_model_configs` | Supported | SDK model and extensions-lib model tests; Tomo closed-tool tests | SDK + Tomo |
| Model download | `client.models.initiate_model_download` | `manage_kamiwaza_demo.ensure_model_download` with preview/apply and bounded projections | Supported; apply is approval-gated | SDK model tests; Tomo direct-SDK and staging tests | SDK + Tomo |
| Model deployment and inference | `client.serving.deploy_model`, deployment list/get/status/wait; OpenAI-compatible client | `manage_kamiwaza_demo.ensure_model_deployment`; `inspect_kamiwaza` deployment reads; extensions-lib model client for inference | Supported; a ready model/runtime is a prerequisite | SDK serving tests; Tomo SDK-tool and platform-model tests | SDK + Tomo; fresh proof blocked by ENG-10492 |
| Connector instance administration | `client.connectors.list`, `list_available`, `get`, `create`, `update`, `register_type`, `subscribe`, `delete` | Read-only `inspect_kamiwaza.list_connectors`; generic OpenAPI connector writes only when selected by deployment policy | Supported for inspection; writes are policy/prerequisite | SDK connector tests; Tomo capability-catalog and OpenAPI tests | SDK + Tomo |
| Connector surface discovery and use | No SDK methods for workroom surface catalog, verify, browse, search, or content fetch | Tomo connector catalog, verification, per-surface tools, and federated connector RAG call Core directly with request-scoped `httpx` | **Missing SDK contract** | Tomo connector tests prove current envelope, bounds, and fail-closed behavior; they do not prove an SDK route | [ENG-10518](https://linear.app/kamiwaza/issue/ENG-10518) |
| Connector provider OAuth and secrets | SDK exposes admin connector contracts, but not an interactive provider-login ceremony | Provider authorization remains a Core/UI flow; Tomo receives only opaque handles and forwarded identity | Deliberately out of scope for the agent; connected provider is a prerequisite | Connector tests confirm provider credentials are not accepted or persisted by Tomo | Core connector UI + policy |
| Skills Library catalog/package lifecycle | `client.skills.list_skills`, `get_skill`, import/download/export/update/delete | No direct SDK tool on the default agent; `openapi_tag:skills` can be enabled through deployment policy; local agent bundles are a separate Kaizen package system | **SDK exists, not exposed** on the default path | SDK Skills Library tests pass; Tomo OpenAPI policy tests prove opt-in discovery but not default consumption | [ENG-9983](https://linear.app/kamiwaza/issue/ENG-9983) |
| Context collections | `client.context.list/create/get/delete_collection` | No default direct-SDK registration; generic Context OpenAPI and specialized bundles are policy-selected | **SDK exists, not exposed** | SDK Context tests pass; no ordinary-user fresh-install proof | [ENG-10505](https://linear.app/kamiwaza/issue/ENG-10505) |
| Context ingest and pipeline lifecycle | `client.context.upload_file`, raw-file CRUD, pipeline/import options, source import, item replay/retry/rerun/cancel, OmniParse lifecycle | No default direct-SDK registration; specialized Context tooling is separately enabled | **SDK exists, not exposed**; storage/parser/embedding resources are prerequisites | SDK Context tests pass; no ordinary-user fresh-install ingest proof | [ENG-10505](https://linear.app/kamiwaza/issue/ENG-10505) |
| Context retrieval | `client.context.search`, `retrieve`, `agentic_search`, vector queries | A reviewed `KamiwazaRetrieveTool` and generic Context OpenAPI route exist, but an empty deployment selection is fail-closed and the default agent receives neither | **SDK exists, not exposed** on a fresh default configuration | SDK Context tests and Tomo OpenAPI/retrieval contract tests pass; runtime default proof remains absent | [ENG-10505](https://linear.app/kamiwaza/issue/ENG-10505) |
| Context ontology | `client.context` ontology list/get/create/delete, add knowledge/entity, search, memory, episodes, group delete, health | No default direct-SDK registration; Ontology Builder is a specialized bundle | **SDK exists, not exposed**; unrestricted ontology mutation is deliberately not implicit | SDK Context tests pass; no default ontology round-trip proof | [ENG-10505](https://linear.app/kamiwaza/issue/ENG-10505) |
| Authentication | `client.auth` password login/refresh/logout/current-user/forward-auth; extensions-lib identity helpers | Tomo validates the signed request identity and constructs a `KamiwazaClient` with a closed forwarded-envelope authenticator | Supported; identity-provider and user administration are deliberately outside the ordinary agent | SDK auth and extensions-lib auth tests; Tomo tool tests verify exact header forwarding | SDK + Tomo |
| Authorization | `client.authz` tuple upsert/delete/object delete/access check | Core remains final authority; Tomo tools carry the validated caller envelope and classify actor-private/bound operations; mutations retain confirmation gates | Supported request boundary; authorization administration is deliberately out of scope | Static SDK service inspection plus Tomo SDK/connector policy tests | SDK + Core + Tomo |
| Tenant and workroom scope | `client.workrooms` CRUD/enter/leave/export/ingestion summary; client workroom-scope recovery | `_platform_context` binds API base, verified forwarded headers, and current workroom; SDK projections never accept caller-authored endpoint or credential fields | Supported; membership is a prerequisite | SDK workroom/scope tests and Tomo SDK-tool tests | SDK + Core + Tomo |
| App, extension, and runtime discovery | `client.apps`, `client.extensions`, `client.serving`, and model deployment reads | `inspect_kamiwaza` exposes app deployments, extensions/status, deployments, models, datasets, and workrooms; `manage_kamiwaza_demo` exposes bounded ensure operations | Supported; install/deploy writes are approval-gated | SDK serving/app/extension tests and Tomo direct-SDK tests | SDK + Tomo; fresh proof blocked by ENG-10492 |

## Verification evidence

### Static and contract evidence

- SDK focused suite: **315 passed**. It covered Context, Skills Library,
  connectors, auth, serving, workrooms, models, workroom-scope recovery, and the
  extensions-lib identity/model helpers.
- Tomo focused suite: **73 passed**. It covered the closed SDK tools, SDK runtime
  grants, OpenAPI broker/runtime policy, connector surfaces, platform model
  inventory, and the member capability catalog.
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

## Follow-up ownership

- Context default-path findings are folded into
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
