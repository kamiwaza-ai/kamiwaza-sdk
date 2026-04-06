# {{name}}

{{description}}

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
  - `src/app/` — Pages and API routes
  - `src/components/` — Reusable UI components
  - `start.mjs` — Runtime entrypoint for dynamic base path support
- `backend/` — FastAPI application with Kamiwaza extensions-lib
  - `app/main.py` — API endpoints with auth, session management, and model access
- `kamiwaza.json` — Extension metadata
- `docker-compose.yml` — Local development configuration

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

## Customization

### Frontend

The frontend uses Tailwind CSS with a Kamiwaza dark theme. Theme colors are defined in `frontend/tailwind.config.ts` under the `kw` prefix. Component classes (`.card`, `.btn-primary`, `.terminal-header`, etc.) are in `frontend/src/app/globals.css`.

### Backend

The backend includes session management (`/session`, `/auth/login-url`, `/auth/logout`), model access (`/api/models`), and an example chat endpoint (`/api/chat`). Add your own endpoints in `backend/app/main.py`.

### Authentication

Authentication is handled by the Kamiwaza platform. The generated code uses `SessionProvider` and `AuthGuard` on the frontend and `require_auth` on the backend. During local development, `KAMIWAZA_USE_AUTH=false` provides an anonymous session.
