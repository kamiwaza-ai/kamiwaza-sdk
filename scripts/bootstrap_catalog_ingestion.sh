#!/usr/bin/env bash
# Bootstraps the catalog ingestion stack + runs ingestion so datasets are ready for retrieval.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_DIR="${STACK_DIR:-$ROOT_DIR/tests/integration/catalog_stack}"
COMPOSE_FILE="${INGESTION_STACK_COMPOSE:-$STACK_DIR/docker-compose.yml}"
SETUP_SCRIPT="${STACK_DIR}/setup-test-data.sh"

ENV_FILE=""
if [[ -z "${KAMIWAZA_API_KEY:-}" && -z "${KAMIWAZA_USERNAME:-}" && -z "${KAMIWAZA_PASSWORD:-}" ]]; then
    if [[ -f "$ROOT_DIR/.env.local" ]]; then
        ENV_FILE="$ROOT_DIR/.env.local"
    elif [[ -f "$ROOT_DIR/.env" ]]; then
        ENV_FILE="$ROOT_DIR/.env"
    fi
fi

load_env_file() {
    local file="$1"
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"
        [[ -z "$line" || "$line" == \#* ]] && continue
        if [[ "$line" == export\ * ]]; then
            line="${line#export }"
        fi
        [[ "$line" != *=* ]] && continue
        local key="${line%%=*}"
        local value="${line#*=}"
        key="${key%%[[:space:]]*}"
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"
        if [[ "$value" == \"*\" && "$value" == *\" ]]; then
            value="${value:1:-1}"
        elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
            value="${value:1:-1}"
        fi
        case "$key" in
            KAMIWAZA_*|CATALOG_STACK_*|KAFKA_BOOTSTRAP)
                if [[ -z "${!key:-}" && -n "$value" ]]; then
                    export "$key=$value"
                fi
                ;;
        esac
    done < "$file"
}

if [[ -n "$ENV_FILE" ]]; then
    echo "Loading KAMIWAZA_* overrides from $ENV_FILE"
    load_env_file "$ENV_FILE"
fi

KAMIWAZA_BASE_URL="${KAMIWAZA_BASE_URL:-https://localhost/api}"
MINIO_PORT="${CATALOG_STACK_MINIO_PORT:-19100}"
MINIO_ENDPOINT="${CATALOG_STACK_MINIO_ENDPOINT:-http://localhost:${MINIO_PORT}}"
MINIO_BUCKET="${CATALOG_STACK_MINIO_BUCKET:-kamiwaza-test-bucket}"
MINIO_PREFIX="${CATALOG_STACK_MINIO_PREFIX:-catalog-tests}"

POSTGRES_HOST="${CATALOG_STACK_POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${CATALOG_STACK_POSTGRES_PORT:-15432}"
POSTGRES_DB="${CATALOG_STACK_POSTGRES_DB:-kamiwaza}"
POSTGRES_USER="${CATALOG_STACK_POSTGRES_USER:-kamiwaza}"
POSTGRES_PASSWORD="${CATALOG_STACK_POSTGRES_PASSWORD:-kamiwazaGetY0urCape}"
POSTGRES_SCHEMA="${CATALOG_STACK_POSTGRES_SCHEMA:-public}"

KAFKA_BOOTSTRAP="${CATALOG_STACK_KAFKA_BOOTSTRAP:-localhost:29092}"

FILE_INGEST_ROOT="${KAMIWAZA_FILE_INGEST_ROOT:-}"

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1" >&2
        exit 1
    fi
}

ensure_docker_runtime() {
    if docker info >/dev/null 2>&1; then
        return 0
    fi

    if [[ -n "${KAMIWAZA_DOCKER_HOST:-}" ]]; then
        export DOCKER_HOST="${KAMIWAZA_DOCKER_HOST}"
        if docker info >/dev/null 2>&1; then
            echo "Using container runtime via KAMIWAZA_DOCKER_HOST=${DOCKER_HOST}"
            return 0
        fi
    fi

    local podman_socket="${HOME}/.local/share/containers/podman/machine/podman.sock"
    if [[ -e "$podman_socket" ]]; then
        export DOCKER_HOST="unix://${podman_socket}"
        if docker info >/dev/null 2>&1; then
            echo "Using Podman socket via DOCKER_HOST=${DOCKER_HOST}"
            return 0
        fi
    fi

    echo "Unable to reach a container runtime with docker CLI." >&2
    echo "Set KAMIWAZA_DOCKER_HOST or DOCKER_HOST to a reachable Docker/Podman socket." >&2
    return 1
}

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
    if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
        PYTHON_BIN="${VIRTUAL_ENV}/bin/python"
    else
        PYTHON_BIN="$(command -v python3 || command -v python || true)"
    fi
fi

if [[ -z "$PYTHON_BIN" ]]; then
    echo "Python interpreter not found. Set PYTHON_BIN or ensure python3 is on PATH." >&2
    exit 1
fi
export PYTHON_BIN

require_python_modules() {
    "$PYTHON_BIN" - <<'PY'
import importlib
import os
missing = []
for mod in ("boto3",):
    try:
        importlib.import_module(mod)
    except Exception:
        missing.append(mod)
if missing:
    print("Missing Python packages: " + ", ".join(missing))
    python_bin = os.environ.get("PYTHON_BIN", "python3")
    print("Install with: " + python_bin + " -m pip install " + " ".join(missing))
    raise SystemExit(1)
PY
}

require_command docker
ensure_docker_runtime

if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "Missing catalog stack compose file: $COMPOSE_FILE" >&2
    exit 1
fi

if [[ ! -f "$SETUP_SCRIPT" ]]; then
    echo "Missing catalog stack seed script: $SETUP_SCRIPT" >&2
    exit 1
fi

echo "Starting catalog stack via docker compose..."
docker compose -f "$COMPOSE_FILE" up -d

echo "Verifying Python dependencies for seeding..."
require_python_modules

echo "Seeding MinIO/Postgres/Kafka fixtures..."
INGESTION_STACK_COMPOSE="$COMPOSE_FILE" \
PYTHON_BIN="$PYTHON_BIN" \
STATE_DIR="$STACK_DIR/state" \
DATA_DIR="$STACK_DIR/data" \
MINIO_ENDPOINT="$MINIO_ENDPOINT" \
MINIO_BUCKET="$MINIO_BUCKET" \
MINIO_PREFIX="$MINIO_PREFIX" \
POSTGRES_HOST="$POSTGRES_HOST" \
POSTGRES_PORT="$POSTGRES_PORT" \
POSTGRES_DB="$POSTGRES_DB" \
POSTGRES_USER="$POSTGRES_USER" \
POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
KAFKA_EXTERNAL_BOOTSTRAP="$KAFKA_BOOTSTRAP" \
FORCE_SEED=1 \
SKIP_INLINE_PARQUET="${SKIP_INLINE_PARQUET:-0}" \
bash "$SETUP_SCRIPT"

export KAMIWAZA_VERIFY_SSL="${KAMIWAZA_VERIFY_SSL:-false}"

echo "Running ingestion jobs against ${KAMIWAZA_BASE_URL}..."
PYTHONPATH="$ROOT_DIR" \
KAMIWAZA_BASE_URL="$KAMIWAZA_BASE_URL" \
KAMIWAZA_API_KEY="${KAMIWAZA_API_KEY:-}" \
KAMIWAZA_USERNAME="${KAMIWAZA_USERNAME:-}" \
KAMIWAZA_PASSWORD="${KAMIWAZA_PASSWORD:-}" \
MINIO_ENDPOINT="$MINIO_ENDPOINT" \
MINIO_BUCKET="$MINIO_BUCKET" \
MINIO_PREFIX="$MINIO_PREFIX" \
POSTGRES_HOST="$POSTGRES_HOST" \
POSTGRES_PORT="$POSTGRES_PORT" \
POSTGRES_DB="$POSTGRES_DB" \
POSTGRES_USER="$POSTGRES_USER" \
POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
POSTGRES_SCHEMA="$POSTGRES_SCHEMA" \
KAFKA_BOOTSTRAP="$KAFKA_BOOTSTRAP" \
FILE_INGEST_ROOT="$FILE_INGEST_ROOT" \
"$PYTHON_BIN" - <<'PY'
import os
import sys

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.authentication import UserPasswordAuthenticator


def _client() -> KamiwazaClient:
    base_url = os.environ["KAMIWAZA_BASE_URL"]
    api_key = os.environ.get("KAMIWAZA_API_KEY", "").strip()
    if api_key:
        return KamiwazaClient(base_url, api_key=api_key)

    username = os.environ.get("KAMIWAZA_USERNAME", "").strip()
    password = os.environ.get("KAMIWAZA_PASSWORD", "").strip()
    if not username or not password:
        print("Set KAMIWAZA_API_KEY or KAMIWAZA_USERNAME/KAMIWAZA_PASSWORD", file=sys.stderr)
        raise SystemExit(1)

    client = KamiwazaClient(base_url)
    client.authenticator = UserPasswordAuthenticator(username, password, client.auth)
    return client


def _patch_endpoint(client: KamiwazaClient, urn: str, endpoint: str) -> None:
    dataset = client.get("/catalog/datasets/by-urn", params={"urn": urn})
    props = dict(dataset.get("properties") or {})
    props.setdefault("endpoint", endpoint)
    props.setdefault("endpoint_url", endpoint)
    props.setdefault("endpoint_override", endpoint)
    props.setdefault("region", "us-east-1")
    client.patch("/catalog/datasets/by-urn", params={"urn": urn}, json={"properties": props})


def main() -> None:
    client = _client()
    minio_endpoint = os.environ["MINIO_ENDPOINT"]
    minio_bucket = os.environ["MINIO_BUCKET"]
    minio_prefix = os.environ["MINIO_PREFIX"]

    response = client.ingestion.run_active(
        "s3",
        bucket=minio_bucket,
        prefix=minio_prefix,
        recursive=True,
        endpoint_url=minio_endpoint,
        region="us-east-1",
        aws_access_key_id="minioadmin",
        aws_secret_access_key="minioadmin",
    )
    s3_urns = response.urns or []
    if s3_urns:
        for urn in s3_urns:
            _patch_endpoint(client, urn, minio_endpoint)
    print("S3 ingestion URNs:", s3_urns)

    response = client.ingestion.run_active(
        "postgres",
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ["POSTGRES_PORT"]),
        database=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        schema=os.environ["POSTGRES_SCHEMA"],
    )
    print("Postgres ingestion URNs:", response.urns or [])

    response = client.ingestion.run_active(
        "kafka",
        bootstrap_servers=os.environ["KAFKA_BOOTSTRAP"],
    )
    print("Kafka ingestion URNs:", response.urns or [])

    file_root = os.environ.get("FILE_INGEST_ROOT", "").strip()
    if not file_root:
        for candidate in (
            os.path.expanduser("~/code/kamiwaza/tests/integration/services/ingestion/docker/state/test-data"),
            os.path.expanduser("~/kamiwaza/tests/integration/services/ingestion/docker/state/test-data"),
        ):
            if os.path.exists(candidate):
                file_root = candidate
                break

    if file_root:
        response = client.ingestion.run_active(
            "file",
            path=file_root,
            recursive=True,
        )
        print("File ingestion URNs:", response.urns or [])
    else:
        print("File ingestion skipped (set KAMIWAZA_FILE_INGEST_ROOT to enable).")


if __name__ == "__main__":
    main()
PY

cat <<EOF

Ingestion complete. Stack is still running for retrieval/MCP testing.

MinIO endpoint: ${MINIO_ENDPOINT}
MinIO bucket:   ${MINIO_BUCKET}
MinIO prefix:   ${MINIO_PREFIX}
Kafka bootstrap: ${KAFKA_BOOTSTRAP}
Postgres: ${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}

To tear down the stack later:
  docker compose -f "${COMPOSE_FILE}" down -v
EOF
