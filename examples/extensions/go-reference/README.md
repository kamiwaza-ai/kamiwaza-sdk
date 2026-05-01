# Go reference extension (non-SDK)

A working Go implementation of the contract in
[`docs/extensions/non-sdk-flow.md`](../../../docs/extensions/non-sdk-flow.md).
This is the canonical reference for authors building Kamiwaza extensions in
languages other than Python or TypeScript — every section here mirrors a
section of the flow doc.

> **Status:** scaffold-equivalent reference. Header parsing only — no HMAC,
> no shared secret. Trust boundary is Traefik (flow doc §8). Tracked in
> [ENG-3894](https://linear.app/kamiwaza/issue/ENG-3894).

## Layout

```
go-reference/
├── go.mod
├── main.go                          HTTP server + identity middleware
├── main_test.go                     Middleware + handler tests
├── internal/identity/
│   ├── extractor.go                 Header → Identity (no crypto)
│   └── extractor_test.go            Consumes the canonical test vectors + edge cases
├── Dockerfile                       Multi-stage build → distroless runtime
├── docker-compose.yml
└── kamiwaza.json                    type: tool
```

## Run locally

```bash
docker compose up -d --build
HOST_PORT=$(docker compose port tool 8000 | cut -d: -f2)

# Health
curl -s "http://localhost:${HOST_PORT}/health"
# → {"status":"ok"}

# Sample tool — anonymous mode (KAMIWAZA_USE_AUTH=false in compose default).
curl -s -XPOST "http://localhost:${HOST_PORT}/tools/echo" \
  -H "Content-Type: application/json" \
  -d '{"message":"hello"}'
# → {"echo":"hello","identity":null}

# Sample tool — auth-on with a simulated envelope.
KAMIWAZA_USE_AUTH=true docker compose up -d --build
HOST_PORT=$(docker compose port tool 8000 | cut -d: -f2)
curl -s -XPOST "http://localhost:${HOST_PORT}/tools/echo" \
  -H "Content-Type: application/json" \
  -H "X-User-Id: u1" \
  -H "X-User-Email: alice@example.com" \
  -H "X-User-Roles: member,editor" \
  -H "X-Workroom-Id: w1" \
  -H "X-User-Workroom-Role: editor" \
  -H "X-Auth-Token: dev-fake-jwt" \
  -d '{"message":"hello"}'
# → 200 {"echo":"hello","identity":{"user_id":"u1",...}}

# Missing X-User-Id under auth-on → canonical misbound_auth.
curl -i -XPOST "http://localhost:${HOST_PORT}/tools/echo" \
  -H "Content-Type: application/json" \
  -H "X-Workroom-Id: w1" \
  -d '{"message":"hello"}'
# → 401 {"error":{"class":"misbound_auth","message":"Required envelope header X-User-Id missing or empty"}}
```

## Run the parity tests

The extractor tests consume
[`docs/extensions/non-sdk-flow/test-vectors.json`](../../../docs/extensions/non-sdk-flow/test-vectors.json)
— the same fixture the Python and TypeScript runtime libs use. A behavior
divergence between languages fails the corresponding case in all three
suites simultaneously.

```bash
# From the repo root or this directory.
go test ./...
```

## Section-by-section mapping to the flow doc

| Flow doc §                | Implemented by                                                              |
| ------------------------- | --------------------------------------------------------------------------- |
| §1 Runtime contract       | `main.go` — `0.0.0.0:$PORT`, `/health`, SIGTERM ≤ 30s, JSON logs to stdout. |
| §2 Envelope headers       | `internal/identity/extractor.go` — case-insensitive read via `http.Header`. |
| §3 Identity parsing       | `internal/identity/extractor.go::Extract`. Strict mode raises `MisboundAuthError` on missing `X-User-Id` / `X-Workroom-Id`. |
| §4 Model-access pattern   | Not exercised in this minimal reference — when calling back into the platform, forward the request's `X-Auth-Token` header to `KAMIWAZA_API_URL`. Do not store it on `Identity`. |
| §5 Failure semantics      | `main.go::writeError` — `{"error":{"class","message"}}` body, HTTP 401 for `misbound_auth`. |
| §6 Packaging              | `Dockerfile` (multi-stage → distroless), `docker-compose.yml` (no fixed host port), `kamiwaza.json` (`type: tool`). |
| §7 Publishing             | `kz-ext publish --stage <profile> --revision $(git rev-parse --short HEAD)` — language-agnostic. |
| §8 Trust boundary         | `internal/identity/extractor.go` package doc — no HMAC verification; trust is gateway-asserted. |

## What this reference deliberately does *not* do

- No HMAC verification, no canonicalization, no TTL check, no shared
  secret. The flow doc §3 + §8 walk through why.
- No `X-Auth-Token` field on `Identity`. The bearer credential lives in
  request scope; `Identity` is safe to log and serialize.
- No MCP / FastMCP framing. The Python tool template uses `FastMCP` for
  ergonomics; non-SDK extensions are just HTTP servers — `tools/echo` is a
  plain JSON endpoint demonstrating the contract without dragging in a
  protocol surface that's unrelated to the envelope.
- No `kz_ext_version` field in `kamiwaza.json`. The Python tool template
  stamps it via the scaffolder; for a hand-authored non-SDK reference it
  serves no purpose (CLI compatibility checks fall through to a warning).

## Updating the test vectors

`docs/extensions/non-sdk-flow/test-vectors.json` is the single source of
truth. Add a new vector there; Python, TypeScript, and Go consumers all
pick it up automatically. If you change the schema, update §3 of the flow
doc and the parity tests in all three languages.
