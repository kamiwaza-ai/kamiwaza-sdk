# {{name}}

{{description}}

This starter is a working Kamiwaza AI app. It ships with:

- authenticated session handling
- model discovery through `/api/models`
- a simple chat UI with explicit model selection
- deployment-aware backend routing for `/api/chat`
- `AGENTS.md` and `CLAUDE.md` for AI coding assistants

## Getting Started

```bash
# Install the Kamiwaza CLI
pip install kamiwaza-sdk

# Login to your Kamiwaza instance
kz-ext login <your-instance-url>

# Start local development
kz-ext dev local
```

The frontend will be available at http://localhost:3000 and the backend at http://localhost:8000.

## Structure

- `frontend/` — Next.js application with Tailwind CSS and Kamiwaza extensions-lib
- `frontend/src/app/page.tsx` — model-aware chat starter UI
- `frontend/src/app/api/`, `session/`, `auth/` — proxy routes from frontend to backend
- `backend/` — FastAPI application with Kamiwaza extensions-lib
- `backend/app/main.py` — auth, model discovery, and chat completion routing
- `kamiwaza.json` — Extension metadata
- `docker-compose.yml` — Local development configuration
- `AGENTS.md` — canonical instructions for AI coding assistants
- `CLAUDE.md` — tells Claude Code to follow `AGENTS.md`

## Development

### Local Development

```bash
kz-ext dev local
```

This starts the frontend and backend via Docker Compose with hot reload enabled. The CLI injects Kamiwaza platform environment variables from your active connection.

### Deploy to Cluster

```bash
kz-ext dev
```

This builds Docker images, pushes them, and deploys to your connected Kamiwaza instance.

### Validate

```bash
kz-ext validate
```

Checks that your extension metadata and compose configuration are correct.

### Frontend Build Check

```bash
cd frontend
npm install
npm run build
```

This catches template and runtime-library mismatches before you deploy.

## Customization

### Frontend

The frontend uses Tailwind CSS with a Kamiwaza dark theme. Theme colors are defined in `frontend/tailwind.config.ts` under the `kw` prefix. Component classes (`.card`, `.btn-primary`, `.terminal-header`, chat layout classes, etc.) are in `frontend/src/app/globals.css`.

### Backend

The backend includes session management (`/session`, `/auth/login-url`, `/auth/logout`), model access (`/api/models`), and a deployment-aware chat endpoint (`/api/chat`). Add your own endpoints in `backend/app/main.py`.

### Authentication

Authentication is handled by the Kamiwaza platform. The generated code uses `SessionProvider` and `AuthGuard` on the frontend and `require_auth` on the backend. During local development, `KAMIWAZA_USE_AUTH=false` provides an anonymous session.

## AI Assistant Guidance

If you are using an AI coding assistant, start with `AGENTS.md`. It explains the working request flow, safe edit zones, and the commands to run after changes.
