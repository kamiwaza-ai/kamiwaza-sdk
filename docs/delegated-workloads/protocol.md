# Delegated workload HTTP protocol

The delegated-workload protocol is an HTTP and JSON contract. The Python SDK
implements it, but it is not the protocol boundary. A service written in any
language can participate with an HTTP client, a JSON parser, ES256 JWS support,
and SHA-256 support. It does not need an extension manifest, the extension
operator, Tomo, or the Python SDK.

The current protocol version is `v1`. Core serves it under
`/api/v1/delegated-workloads`. The normative OpenAPI contract is OpenAPI 3.1;
the request/response examples and machine-readable compatibility metadata in
[`conformance-v1.json`](conformance-v1.json) are portable conformance inputs.
See the [quickstart](quickstart.md) for the complete application flow and the
[resource guide](resources.md) for guard and adapter authoring.

## Security boundary

Treat workload assertions, run and effect capabilities, DPoP private keys and
proofs, nonces, broker handles, and effect-consumption tokens as secrets. Keep
them in process memory, redact them from logs and errors, and never put them in
queues or durable application state. Queue messages carry only opaque run
references.

Generic member authentication must reject delegated capability token classes.
A workload obtains an assertion from an approved attestation profile and sends
it only to Core. A protected resource accepts only a dedicated effect
capability and verifies it before application code runs.

Every authority-bearing request uses a fresh DPoP proof:

- The protected header is `typ=dpop+jwt`, `alg=ES256`, with a public P-256
  `jwk`. Never include the private `d` coordinate.
- `htm` is the uppercase method, `htu` is the exact target URI, `iat` is the
  issued-at time, and `jti` is unique.
- `body_sha256` is `sha256:` followed by the lowercase hexadecimal SHA-256 of
  the exact request bytes the effect will execute.
- `ath` is unpadded base64url SHA-256 of the ASCII capability when a capability
  is present.
- A capability's `cnf.jkt` must equal the RFC 7638 thumbprint of the proof JWK.

The fixture's proof vector gives reproducible `htm`, `htu`, `body_sha256`, and
`ath` values. Real conformance runs must generate a fresh key, `iat`, `jti`,
signature, and capability; the angle-bracket values are deliberately inert
placeholders, not usable credentials.

## Protected-resource exchange

A direct guard performs three steps in order. It verifies the effect capability
and exact protected request locally, asks Core for a current side-effect-free
decision, and consumes the returned one-use token. Only then may it invoke the
handler.

### 1. Verify the exact request locally

Verify all of these together:

- token type `kz-effect-cap+jwt`, issuer
  `urn:kamiwaza:delegated-workloads:v1`, trusted `kz-delegated-*` key ID,
  ES256 signature, audience, issue/not-before/expiry/deadline window, and
  `effect:execute` scope;
- member subject and workload actor, revision, role, instance, grant, run,
  claim, effect, fencing token, and proof-key thumbprint;
- exactly one registered resource type, descriptor version and canonical ID,
  plus exactly one action; and
- DPoP method, target URI, access-token hash, exact body digest, freshness, and
  replay identity.

Reject unknown types, versions, actions, audiences, adapters, claims, keys, or
fields. Reject every inbound `X-Kamiwaza-Delegated-*` header; a direct guard
derives context rather than trusting forwarded context.

### 2. Get the current decision

Send the original authority to Core. Do not include a consumption token in this
request:

```http
POST /api/v1/delegated-workloads/effect-authorizations HTTP/1.1
Content-Type: application/json
Authorization: DPoP <effect-capability>
DPoP: <dpop-proof>
X-Kamiwaza-Workload-Assertion: <workload-assertion>

{"effect_id":"33333333-3333-4333-8333-333333333333","method":"POST","request_digest":"sha256:4bd285fb587cc51a7fbd4b15e3856fecb8aff282d44e82af1d1fc48d1b9228c2","target_uri":"https://resource.example.test/documents/doc-7"}
```

An allow response contains a sealed requester context and a sensitive one-use
`consumption_token`. A deny response is still a typed `200` decision, has no
token, and must not invoke the handler. Compare the returned context with every
identity and resource binding already verified from the capability and the
active resource-registration revision.

Mesh authorization uses this same operation but never consumes the effect. It
may forward only the sealed header set declared by the OpenAPI contract after
stripping every inbound delegated header.

### 3. Consume before dispatch

Present the one-use token only to the consumption operation:

```http
POST /api/v1/delegated-workloads/effects/33333333-3333-4333-8333-333333333333/consumption HTTP/1.1
Content-Type: application/json
Authorization: DPoP <effect-capability>
DPoP: <dpop-proof>
X-Kamiwaza-Workload-Assertion: <workload-assertion>
X-Kamiwaza-Effect-Consumption: <one-use-consumption-token>

{"fencing_token":3,"request_digest":"sha256:4bd285fb587cc51a7fbd4b15e3856fecb8aff282d44e82af1d1fc48d1b9228c2"}
```

Invoke the handler only when Core returns `status=executing` and exactly the
same requester context as the allow decision. A missing token, replay, stale
fence, changed context, timeout, audit failure, or any ambiguous response is a
denial. Install a sealed context with separate member subject and workload
actor; never accept either identity from the request body, query, or caller
headers.

After the handler returns, report the terminal transition. If a mutation's
outcome is unknown, report `ambiguous` and never replay it automatically.

## Errors and bounded retry

Core errors use this closed envelope:

```json
{
  "error": {
    "code": "proof_mismatch",
    "message": "safe description",
    "retry_classification": "never",
    "correlation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    "safe_details": {}
  }
}
```

Authentication, capability, and proof failures use `401`; current authority
denials use `403`; replay, fencing, state, and idempotency conflicts use `409`;
registered-contract violations use `422`; unavailable required dependencies
use `503`. The complete code/status/retry table is `error_mapping` in the
fixture. Unknown codes, retry classes, fields, or internally inconsistent
responses fail closed.

On `401` with `error.code=dpop_nonce_required` and a `DPoP-Nonce` header, retry
the same idempotent protocol request once with a new proof containing the
nonce. Never replay protected application work to answer a nonce challenge.

## Compatibility

`v1` supports additive optional response fields. A claim meaning change,
relaxed verification check, new required field, widened default authority, or
other semantic change requires a new contract version. Send supported protocol,
resource descriptor, guard, and adapter versions during discovery. Reject an
unknown or incompatible combination before intent creation, run reservation,
effect execution, or handler dispatch; there is no warning-only fallback.

The fixture publishes the current protocol, descriptor and `guard:v1`
contracts, the direct/ASGI guard adapters, and the resource-adapter interface
versions. Its `negative_guard_cases` are mandatory: every mutation returns the
stable resource denial and leaves `handler_invoked=false`.

## Run the portable fixture checks

The repository test uses only Python's standard library for JSON, hashing, AST
inspection, and raw request construction. It deliberately imports no
`kamiwaza_sdk` module:

```bash
uv run pytest -q tests/contract/delegated_workloads/test_http_protocol_vectors.py
```

Other languages should load the same JSON, reproduce the proof vector, build
the two HTTP requests, preserve the authorization/consumption ordering, check
the closed error map, and apply every negative mutation before connecting to a
live conformance environment.
