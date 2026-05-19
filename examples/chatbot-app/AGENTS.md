# AGENTS.md

This repo is a working Kamiwaza app starter. Treat this file as the canonical
instruction source for AI coding assistants and human contributors.

## Goal

Turn this starter into your app without breaking the working auth, model
selection, and deployment flow that already ships with it.

## Repo Map

- `frontend/src/app/page.tsx`:
  Main authenticated chat UI, model loading, request state, and error display.
- `frontend/src/app/api/[...path]/route.ts`:
  Proxies frontend `/api/*` calls to the backend.
- `frontend/src/app/session/route.ts`:
  Proxies session reads to the backend.
- `frontend/src/app/auth/login-url/route.ts`:
  Proxies login URL lookups to the backend.
- `frontend/src/app/auth/logout/route.ts`:
  Proxies logout requests to the backend.
- `backend/app/main.py`:
  FastAPI routes, auth protection, model discovery, and chat completion logic.
- `README.md`:
  Developer commands, architecture notes, and extension workflow.

## Important Commands

- `kz-ext validate`
- `kz-ext dev local`
- `kz-ext dev`
- `cd frontend && npm install && npm run build`
- `python -m py_compile backend/app/main.py`

## Guardrails

- Keep frontend requests base-path safe for runtime app URLs.
- Keep `/session`, `/auth/login-url`, `/auth/logout`, and `/api/*` proxy routes
  working unless you are intentionally changing the platform integration layer.
- Prefer routing browser model calls through the backend instead of talking to
  platform model endpoints directly from the frontend.
- Use model IDs returned by `/api/models`; do not hard-code deployment paths.
- Surface backend error details in the UI so debugging stays fast.
- Keep chat state in the browser unless you are intentionally adding storage.
- If you change auth or session handling, verify both local development and a
  deployed runtime app flow.

## AI Assistant Notes

- `AGENTS.md` is the canonical instruction source for this repo.
- `CLAUDE.md` points Claude Code back to this file to avoid divergent guidance.
