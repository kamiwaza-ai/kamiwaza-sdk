FROM python:3.12-slim

# NOTE: requirements.txt resolves `kamiwaza-extensions-lib` from the package
# index (whatever is published), NOT the in-repo workspace member at
# `<sdk-root>/kamiwaza_extensions_lib/`. PRs that modify the in-repo
# extensions lib won't be tested through this live contract app until the
# build context is widened to the SDK repo root and the lib is installed
# from the workspace. Tracked as follow-up (see PR #129 description, finding I).

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1001 appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser app ./app

USER 1001:1001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
