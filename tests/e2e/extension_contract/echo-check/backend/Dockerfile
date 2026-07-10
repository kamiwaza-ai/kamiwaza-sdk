FROM python:3.12-slim

# Build context for this image is the SDK repo root (set via
# docker-compose.yml ``context:`` and ``dockerfile:``), so the in-repo
# workspace member ``kamiwaza_extensions_lib/`` can be installed
# directly instead of resolving ``kamiwaza-extensions-lib`` from PyPI.
# This lets PRs that modify the workspace member exercise their changes
# through the live contract app. ENG-5957.

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1001 appuser

WORKDIR /app

# Install the in-repo workspace member first; subsequent
# ``requirements.txt`` install treats ``kamiwaza-extensions-lib`` as
# already-satisfied.
COPY kamiwaza_extensions_lib/ /tmp/kamiwaza_extensions_lib/
RUN pip install --no-cache-dir /tmp/kamiwaza_extensions_lib/

COPY tests/e2e/extension_contract/echo-check/backend/requirements.txt /tmp/req.txt
RUN pip install --no-cache-dir -r /tmp/req.txt

COPY --chown=appuser:appuser tests/e2e/extension_contract/echo-check/backend/app ./app

USER 1001:1001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# --root-path threads $KAMIWAZA_APP_PATH into the ASGI scope so
# trusted_routed_workroom_context() can validate `scope['root_path']`
# against the runtime prefix. Without this, real routed requests
# arrive with root_path="" and identity-header forwarding fails
# silently (returns 403 / drops identity fields).
#
# ENG-5956 follow-up (kamiwaza-sdk#134 self-review H1): root_path alone is
# not sufficient as a trust signal — uvicorn populates it on every request
# including direct container traffic. The trusted-routed path now also
# requires the `x-kamiwaza-trusted-proxy` header to match
# `$KAMIWAZA_TRUSTED_PROXY_SECRET`, which the routing layer (Traefik /
# istio ingress) injects only for routed traffic. The compose file passes
# the secret in via env so the harness can mint matching test headers.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --root-path \"${KAMIWAZA_APP_PATH:-}\""]
