# Neutral protected document resource

This example is a separately deployable protected resource with no extension or
Tomo dependency. It adds a new `conformance.document` audience through an
authorized resource-registration adapter and then uses the public SDK guard for
every protected request. The server never self-registers, classifies its own
effect at request time, accepts a member token, or trusts caller-supplied
delegated context.

The staged descriptor in `resource-registration.json` declares:

- audience `https://documents.example.test` and canonical IDs of the form
  `document:<id>`;
- a read action with no approval and a mutation action requiring exact IT
  approval;
- deterministic canonical request digests, current entitlement, document-
  operation and mutation-byte quota dimensions;
- the closed `document.export` broker operation and safe result normalization;
  and
- guard contract `guard:v1` with explicit v1 compatibility.

An authorized platform resource registrar must reconcile and activate an
immutable revision. Supply the resulting revision ID to the service. The
resource process has no registrar credential and cannot activate or revise the
descriptor.

## Deploy

Build from the SDK repository root so the image installs the branch's SDK:

```bash
export KAMIWAZA_DELEGATED_CORE_URL=https://kamiwaza.example/api/v1/delegated-workloads
export RESOURCE_REGISTRATION_REVISION_ID=bbbbbbbb-cccc-4ddd-8eee-ffffffffffff
export RESOURCE_AUDIENCE=https://documents.example.test
docker compose -f examples/delegated_resource_server/docker-compose.yml up --build
```

The image runs as UID/GID 65532 with a read-only filesystem, no new privileges,
and only a bounded temporary directory. `/healthz` is the sole unguarded route.

## Protected routes

- `GET /v1/documents/<id>` uses action `read`.
- `PUT /v1/documents/<id>` uses action `mutate` and accepts exactly
  `{"title":"..."}` up to 256 characters.

The workload client first reserves the exact effect. Its protected request
carries the effect capability, fresh DPoP proof, and workload assertion. The
ASGI adapter verifies the token and exact method, URI, and bytes locally, asks
Core for a current decision, consumes the one-use token, and installs a sealed
dual-principal context before `DocumentApplication` runs.

The application additionally requires the canonical route ID to equal the
sealed resource ID. Responses expose the document plus safe member-subject and
workload-actor attribution; they never echo capability, proof, assertion,
consumption token, credential, or caller-provided identity.

The JWKS provider caches only public keys for 30 seconds and fails closed on an
invalid or unavailable refresh. Any invalid capability, proof, descriptor,
claim/fence, decision, replay, spoofed delegated header, malformed mutation, or
context mismatch returns the stable protected-resource denial before document
state changes.

The in-memory store is deterministic conformance state, not production
persistence. The full onboarding journey supplies unique test data and cleanup;
real resources keep their existing service and persistence boundaries behind
the same guard and adapters.
