# chatbot-app

This example mirrors the current output of:

```bash
kz-ext create --type app --name chatbot-app
```

It exists for two reasons:

- as a concrete reference app you can inspect without scaffolding first
- as a smoke-test target that helps keep the SDK starter honest

## What It Includes

- authenticated session handling
- model discovery through `/api/models`
- a simple chat UI with explicit model selection
- deployment-aware backend routing for `/api/chat`
- `AGENTS.md` and `CLAUDE.md` for AI coding assistants

## Quick Start

```bash
kz-ext validate
kz-ext dev local
```

The frontend runs on http://localhost:3000 and the backend on http://localhost:8000.

## Notes

- The chat transcript is in-memory only and resets on refresh.
- The frontend intentionally routes chat requests through the backend so model
  auth, endpoint selection, and error handling stay in one place.
- This example is kept intentionally close to the default app starter so
  changes to one should be reflected in the other.
