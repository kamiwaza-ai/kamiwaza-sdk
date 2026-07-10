FROM python:3.10-slim

RUN groupadd -r -g 1001 appuser && useradd -r -u 1001 -g appuser -d /app appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chown -R appuser:appuser /app

USER 1001

EXPOSE 8000

# Shared ASGI launcher: resolves KAMIWAZA_ROUTING_MODE/KAMIWAZA_APP_PATH and
# starts Uvicorn with root_path, so the un-stripped prefixed request routes
# to the app's unprefixed /api and /health declarations. Local dev overrides
# this with --reload via compose (kz-ext dev local).
CMD ["python", "-m", "kamiwaza_extensions_lib.asgi", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
