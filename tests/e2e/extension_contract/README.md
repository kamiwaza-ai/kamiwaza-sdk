# Extension Contract Tests

A standalone harness that drives a real Kamiwaza deployment through the App Garden
template push, deploy, and runtime probe path. Verifies the platform contract that
every extension depends on.

The fixture under test is `echo-check/` — a tiny FastAPI app that exercises the
auth-fronted, path-prefixed routing path without dragging in any extension-specific
behavior. Its main job is to fail fast when the platform contract regresses.

## What the harness proves

1. Templates can be pushed to a running Kamiwaza instance via `/apps/app_templates`.
2. Required images can be pulled (or local fallback honored).
3. Apps can be deployed through App Garden.
4. Deployments reach a ready state with a public `access_path`.
5. Routed HTTP traffic responds successfully through Traefik.
6. The forwarded-identity headers reach the extension without spoofing.
7. The session router and `require_auth` dependency behave per contract.
8. Deployments can be cleaned up.

## Required environment

- A running Kamiwaza deployment (e.g. `make dev-full-local` from `../deploy`)
- Credentials with permission to create templates and deploy apps
- A bootstrap artifact from the canonical deploy target:
  `../deploy/.artifacts/live-routed-integration/bootstrap-state.json`

Environment variables:

- `RUN_LIVE_EXTENSION_TESTS=1`
- `KAMIWAZA_API_URL=https://localhost/api` (or another live API base URL)
- `KAMIWAZA_VERIFY_SSL=false` when using self-signed local TLS
- Either `KAMIWAZA_API_KEY` or `KAMIWAZA_USERNAME` + `KAMIWAZA_PASSWORD`
- `LIVE_ROUTED_INTEGRATION_STATE=../deploy/.artifacts/live-routed-integration/bootstrap-state.json`
  to consume the canonical bootstrap handoff artifact

Optional:

- `LIVE_EXTENSION_SECRET_ENCRYPTION_KEY` to pin a known secret for the test deployment
- `LIVE_EXTENSION_KZ_LOGIN_PATH` to point at a local `deploy/scripts/kz-login` helper when the deploy repo is not adjacent
- `LIVE_EXTENSION_BUILD_EXTENSIONS=1` to force a local docker build before template push
- `LIVE_EXTENSION_DEPLOY_TIMEOUT` to override deploy polling timeout
- `LIVE_EXTENSION_PROBE_TIMEOUT` to override HTTP probe timeout
- `LIVE_EXTENSION_KEEP_DEPLOYMENT=1` to leave the deployed app running for manual follow-up
- `LIVE_EXTENSION_OUTPUT_DIR` to override the deployment-artifact directory

## Running

Canonical cross-repo bootstrap:

```bash
cd ../deploy
make live-integration-bootstrap
```

Run the harness:

```bash
RUN_LIVE_EXTENSION_TESTS=1 pytest tests/e2e/extension_contract/
```

The harness builds echo-check from a clean checkout via `docker build` before
pushing its template, so the one-command flow does not depend on a pre-existing
local image.

## Harness unit tests

The `test_harness_*.py` files exercise the harness machinery itself with mocked
clients and do NOT require a live cluster. They run as part of the normal unit
suite.

## echo-check dependency note

echo-check imports `kamiwaza_extensions_lib` (published on PyPI as
`kamiwaza-extensions-lib`). That library is the canonical successor to the
legacy `kamiwaza_auth` shim that ships in `kamiwaza-extensions-template/shared/python/`.
See `kamiwaza-sdk/kamiwaza_extensions_lib/` for the source.

Artifacts:

- bootstrap handoff: `LIVE_ROUTED_INTEGRATION_STATE` or
  `../deploy/.artifacts/live-routed-integration/bootstrap-state.json`
- deployment artifacts: `.artifacts/live-extensions/<extension>.json`

The deployment artifact includes `app_url`, `readiness_url`, and `smoke_url`
for downstream browser or SDK probes.
