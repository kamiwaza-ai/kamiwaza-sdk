FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE MANIFEST.in ./
COPY kamiwaza_sdk ./kamiwaza_sdk
COPY kamiwaza_client ./kamiwaza_client
COPY kamiwaza_extensions ./kamiwaza_extensions
COPY kamiwaza_extensions_lib ./kamiwaza_extensions_lib
RUN pip install --no-cache-dir ./kamiwaza_extensions_lib \
    && pip install --no-cache-dir . 'uvicorn>=0.30,<1'
COPY examples/delegated_resource_server ./examples/delegated_resource_server

EXPOSE 8080
USER 65532:65532
ENTRYPOINT ["uvicorn", "examples.delegated_resource_server.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
