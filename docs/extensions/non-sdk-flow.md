# Non-SDK extension flow

> **Audience:** Authors building Kamiwaza extensions in languages other than
> Python or TypeScript (Go, Rust, Java, …) who can't use `kamiwaza-extensions-lib`
> or `@kamiwaza-ai/extensions-lib` directly.
>
> **Status:** Canonical contract. Revised 2026-04-23 (verify-in-extension dropped
> per system design §4.4.2 — extensions are pass-through consumers of
> Traefik-populated headers, not crypto verifiers).
>
> **See also:** A working Go reference implementation of this contract
> (`examples/extensions/go-reference/`) is planned — tracked as
> [ENG-3894](https://linear.app/kamiwaza/issue/ENG-3894). Until it lands,
> the canonical test vectors below + the Python and TypeScript reference
> implementations are the source of truth.

This document defines the runtime contract a non-SDK extension must implement
to deploy on the Kamiwaza platform. It is wire-compatible with the Python and
TypeScript runtime libraries — the test vectors at
[`non-sdk-flow/test-vectors.json`](./non-sdk-flow/test-vectors.json) are
consumed bit-identically by all three test suites.

---

## 1. Runtime contract

An extension is a long-running HTTP server. The platform expects:

| Concern | Contract |
| --- | --- |
| **Listen address** | Bind `0.0.0.0:8000` by default (configurable via `PORT` env var). |
| **Health endpoint** | `GET /health` returns HTTP 200 with body `{"status": "ok"}` within 1s. Used by Kubernetes liveness + readiness probes. |
| **Process lifecycle** | Run in foreground; respond to SIGTERM with a graceful shutdown drain (≤ 30s). |
| **Logging** | Write structured logs to stdout. JSON-shape recommended; the platform's log aggregator does not require a specific schema. |

### Environment variables

The platform injects these at deploy time. Treat any as optional unless stated.

| Variable | Required | Description |
| --- | --- | --- |
| `KAMIWAZA_API_URL` | yes | Base URL of the platform API (e.g. `http://api:7777`). Use this to call back into the platform. |
| `KAMIWAZA_USE_AUTH` | yes | `"true"` in production; `"false"` for local-dev mode. When false, identity-extraction returns an anonymous `Identity`. |
| `KAMIWAZA_EXTENSION_NAME` | yes | The deployed extension's name (matches `kamiwaza.json.name`). |
| `KAMIWAZA_EXTENSION_VERSION` | yes | Semver of the deployed image. |
| `PORT` | no | HTTP listen port (default `8000`). |
| `LOG_LEVEL` | no | One of `debug`, `info`, `warn`, `error` (default `info`). |

---

## 2. Envelope headers

When `KAMIWAZA_USE_AUTH=true`, every authenticated request reaches the
extension via Traefik's ForwardAuth middleware, which stamps the following
headers on success:

| Header | Type | Notes |
| --- | --- | --- |
| `X-User-Id` | UUID string | Always set on authenticated requests. **Required.** |
| `X-User-Email` | string | RFC-5322 email. May be empty for service accounts. |
| `X-User-Name` | string | Display name. Free-form. |
| `X-User-Roles` | comma-separated list | E.g. `member,editor,admin`. Empty string if no roles. |
| `X-User-System-High` | string | Platform classification token (`U`, `TS`, …). NOT a boolean. |
| `X-Workroom-Id` | UUID string | Active workroom. **Required.** All-`f` UUID = global sentinel. |
| `X-User-Workroom-Id` | UUID string | User-scoped alias of `X-Workroom-Id` during the migration window. Read either; both will be present. |
| `X-User-Workroom-Role` | string | Workroom-scoped role (e.g. `editor`). |
| `X-Auth-Token` | bearer token | Forward to the platform when calling back. Do **not** parse or store. |
| `X-Request-Id` | string | Trace correlation. Echo back in response logs. |
| `X-User-Signature` + `X-User-Signature-Ts` | string + epoch ms | HMAC pair attached by the auth service. Extensions **do not verify** these (see §8). They will be present in the request — ignore them. |

Headers are case-insensitive on the wire (HTTP/1.1 §3.2). Extensions should
read them via a case-insensitive map.

---

## 3. Identity parsing

The contract: read the headers above into an `Identity` value. **Header parsing
only — no HMAC verification, no shared secret, no canonicalization, no TTL
check.** The trust boundary is Traefik (see §8); intra-pod parsing is
plain string handling.

### Algorithm

```
extract_identity(headers):
  user_id     = headers["X-User-Id"]
  workroom_id = headers["X-Workroom-Id"]
  if not user_id or not workroom_id:
    raise misbound_auth ("Required envelope header missing or empty")
  return Identity{
    user_id:       user_id,
    email:         headers.get("X-User-Email", ""),
    name:          headers.get("X-User-Name", ""),
    roles:         split_csv(headers.get("X-User-Roles", "")),
    system_high:   headers.get("X-User-System-High", ""),
    workroom_id:   workroom_id,
    workroom_role: headers.get("X-User-Workroom-Role", ""),
    request_id:    headers.get("X-Request-Id", ""),
  }
```

`X-Auth-Token` is deliberately **not** stored on `Identity` — keep it in
request scope only and read it directly from the headers when forwarding to
the platform. Persisting the bearer credential in a serializable struct
risks logging it.

### Test vectors

The four canonical vectors at
[`non-sdk-flow/test-vectors.json`](./non-sdk-flow/test-vectors.json) are
the parity contract. Your test suite should consume the same JSON file
and assert identical behavior:

| Case | Outcome |
| --- | --- |
| `happy-path` | Returns `Identity` with all fields populated. |
| `missing-user-id` | Raises `misbound_auth`. |
| `missing-workroom` | Raises `misbound_auth`. |
| `global-workroom-sentinel` | Returns `Identity` with `workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff"`. |

---

## 4. Model-access pattern

When your extension calls into a model, route the call through the platform's
dispatch boundary — do **not** open direct connections to model providers
with passthrough user tokens.

```
extension                      platform                       model
   │                              │                              │
   │  POST /v1/chat/completions   │                              │
   │   X-Auth-Token: <user-jwt>   │                              │
   │ ──────────────────────────► │                              │
   │                              │  POST /openai/...            │
   │                              │   Authorization: <svc>       │
   │                              │ ──────────────────────────► │
   │                              │ ◄────────────── stream ──── │
   │ ◄──────── pass-through ───── │                              │
```

The dispatch boundary emits the audit event with end-user attribution
(workroom + user_id). Extensions never authenticate to model providers
themselves.

---

## 5. Failure semantics

Five canonical failure classes. Each maps to an HTTP status and a class name
the runtime libs surface in logs and structured errors.

| Class | HTTP | When |
| --- | --- | --- |
| `misbound_auth` | 401 | Required envelope header missing or empty (caught by §3). |
| `unexpected_context` | 401 | Envelope shape mismatch (e.g. local-dev shape arriving in prod). |
| `out_of_envelope_access` | 403 | Extension attempted access outside the envelope's workroom scope. |
| `platform_outage` | 502 | Platform API unreachable or returning 5xx. |
| `kamiwaza_runtime_error` | 500 | Catch-all base class. |

Return JSON: `{"error": {"class": "<class_name>", "message": "<safe-message>"}}`.
Never include stack traces, internal hostnames, or upstream response bodies in
the message.

---

## 6. Packaging

Minimum `kamiwaza.json`:

```json
{
  "name": "my-extension",
  "version": "0.1.0",
  "type": "tool",
  "description": "One-line summary"
}
```

Minimum `docker-compose.yml`:

```yaml
services:
  app:
    build: .
    ports:
      - "8000"
    environment:
      KAMIWAZA_USE_AUTH: ${KAMIWAZA_USE_AUTH:-true}
      KAMIWAZA_API_URL: ${KAMIWAZA_API_URL}
```

Bind container port `8000` (no fixed host port — the platform allocates one).
Use `Dockerfile` for build; multi-stage builds with a distroless runtime are
recommended for non-Python extensions.

---

## 7. Publishing

`kz-ext publish` is language-agnostic. From the extension repo root:

```
kz-ext publish --profile <profile> [--revision <sha>]
```

The CLI builds the image, pushes to the configured registry, and publishes
the catalog entry. No Python/TS-specific steps in the publish flow — the
`kamiwaza.json` + `docker-compose.yml` + image tag are the contract.

Set `--revision` to your CI's git SHA so re-publishes are idempotent (per
the catalog dedup guard, ENG-3884 / M1).

---

## 8. Trust boundary and known gaps

Traefik's ForwardAuth middleware is the trust boundary. An extension that
receives a request trusts that Traefik routed it from an authenticated
caller — that's why §3 is plain header parsing, not crypto verification.

**Known gap:** intra-cluster pod-to-pod requests (one extension calling
another, or any in-cluster process forging Traefik headers) are not
currently authenticated by the platform. This is tracked separately as the
Istio mTLS follow-on (outside D210 scope). Operators deploying extensions
in multi-tenant or less-trusted clusters should be aware of this
limitation.

The HMAC pair (`X-User-Signature` / `X-User-Signature-Ts`) emitted by the
auth service is platform-internal integrity (covered by INF-A1 contract
test in `kamiwaza/services/auth/tests/unit/test_forwardauth_contract.py`).
Extension-side verification was considered and intentionally dropped: it
duplicated platform-internal checks, required a shared secret extensions
don't otherwise need, and didn't address the actual threat model
(intra-cluster forgery, which is a network-level concern).
