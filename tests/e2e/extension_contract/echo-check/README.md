# Echo Check

Internal App Garden app used by the extension contract harness.

`echo-check` is intentionally small. Its job is to fail fast when the
Kamiwaza-side platform contract regresses — routed prefix handling, forwarded
auth, env injection, and the standard session router under `/api/session`.

What it proves:

- routed root path responds under the deployment access path
- `KAMIWAZA_APP_PATH` and `KAMIWAZA_DEPLOYMENT_ID` are injected into the app
- the standard session router works under `/api/session`
- protected endpoints accept Kamiwaza forwarded auth and routed workroom context
- direct (non-routed) container access is NOT trusted as an auth path

## Layout

- `backend/` — FastAPI app, tests, Dockerfile
- `docker-compose.yml` — source compose file for local builds
- `docker-compose.appgarden.yml` — App Garden compose used at deploy time

## Build

The harness builds echo-check via `docker build` against the `backend/Dockerfile`
before pushing the template. There's no separate prebuild step — the Dockerfile
installs `kamiwaza-extensions-lib` from PyPI directly.

To build manually:

```bash
docker build -t kamiwazaai/echo-check-app:0.2.0-dev -f backend/Dockerfile backend
```

## Dependency note

The app imports `kamiwaza_extensions_lib` (PyPI: `kamiwaza-extensions-lib`).
That library is the canonical successor to the legacy `kamiwaza_auth` shim
that ships in `kamiwaza-extensions-template/shared/python/`. Source lives in
`kamiwaza-sdk/kamiwaza_extensions_lib/`.
