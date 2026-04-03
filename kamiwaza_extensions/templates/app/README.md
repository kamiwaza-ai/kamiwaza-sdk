# {{name}}

{{description}}

## Getting Started

```bash
# Install the Kamiwaza CLI
pip install kamiwaza-extensions

# Login to your Kamiwaza instance
kz-ext login <your-instance-url>

# Start local development
kz-ext dev local

# Run as the active Kamiwaza connection user on localhost
kz-ext dev local --auth
```

## Structure

- `frontend/` — Next.js application with Kamiwaza extensions-lib
- `backend/` — FastAPI application with Kamiwaza extensions-lib
- `kamiwaza.json` — Extension metadata
- `docker-compose.yml` — Local development configuration
