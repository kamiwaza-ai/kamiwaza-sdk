#!/bin/bash
# Reproducibly seed the local ingestion stack with sample data.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${STATE_DIR:-$SCRIPT_DIR/state}"
DATA_DIR="${DATA_DIR:-$SCRIPT_DIR/data}"
export STATE_DIR DATA_DIR
COMPOSE_FILE="${INGESTION_STACK_COMPOSE:-$SCRIPT_DIR/docker-compose.yml}"
GENERATOR_SCRIPT="${SCRIPT_DIR}/generate-parquet-data.py"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://localhost:19100}"
MINIO_BUCKET="${MINIO_BUCKET:-kamiwaza-test-bucket}"
MINIO_PREFIX="${MINIO_PREFIX:-catalog-tests}"
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-15432}"
POSTGRES_DB="${POSTGRES_DB:-kamiwaza}"
POSTGRES_USER="${POSTGRES_USER:-kamiwaza}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-kamiwazaGetY0urCape}"
KAFKA_BOOTSTRAP="${KAFKA_EXTERNAL_BOOTSTRAP:-localhost:29092}"
SEED_MARKER="$STATE_DIR/.seed-complete"

mkdir -p "$STATE_DIR" "$STATE_DIR/test-data"

if [[ ! -d "$DATA_DIR/test-data" ]]; then
    echo "Catalog test data not found at $DATA_DIR/test-data; generating defaults..."
    mkdir -p "$DATA_DIR/test-data/objects"
    cat > "$DATA_DIR/test-data/objects/sample.json" <<'JSON'
{"source":"sdk-catalog-stack","records":[{"id":1,"store":"downtown"},{"id":2,"store":"uptown"}]}
JSON

    if [[ -f "$GENERATOR_SCRIPT" ]]; then
        if "$PYTHON_BIN" "$GENERATOR_SCRIPT" --output-dir "$DATA_DIR/test-data"; then
            echo "Generated parquet fixtures via $GENERATOR_SCRIPT."
        else
            echo "Warning: parquet generation script failed; relying on inline fallback generation." >&2
        fi
    fi
fi

if [[ "${FORCE_SEED:-0}" != "1" && -f "$SEED_MARKER" ]]; then
    echo "Ingestion stack already seeded (set FORCE_SEED=1 to reseed)."
    exit 0
fi

echo "Syncing sample data into state directory..."
rm -rf "$STATE_DIR/test-data"
if command -v rsync >/dev/null 2>&1; then
    rsync -a "$DATA_DIR/test-data/" "$STATE_DIR/test-data/"
else
    cp -R "$DATA_DIR/test-data" "$STATE_DIR/"
fi

INLINE_SMALL="$STATE_DIR/test-data/inline-small.parquet"
INLINE_LARGE="$STATE_DIR/test-data/inline-large.parquet"
FALLBACK_PARQUET="$STATE_DIR/test-data/sales_data_10k.parquet"

if [[ "${SKIP_INLINE_PARQUET:-0}" == "1" ]]; then
    echo "Skipping inline parquet generation (SKIP_INLINE_PARQUET=1)."
else
    echo "Generating inline threshold parquet fixtures..."
    if "$PYTHON_BIN" - <<'PY'
import importlib
import sys
missing = []
for mod in ("pandas", "numpy", "pyarrow"):
    try:
        importlib.import_module(mod)
    except Exception:
        missing.append(mod)
if missing:
    print("Missing optional modules for inline parquet generation: " + ", ".join(missing))
    sys.exit(1)
PY
    then
        "$PYTHON_BIN" - <<'EOF'
import os
from pathlib import Path
import pandas as pd
import numpy as np

state_dir = Path(os.environ.get("STATE_DIR", ".")) / "test-data"
state_dir.mkdir(parents=True, exist_ok=True)

def generate_parquet(path: Path, target_bytes: int) -> None:
    rows = 1024
    while True:
        alphabet = list("abcdefghijklmnopqrstuvwxyz0123456789")
        payload = ["".join(np.random.choice(alphabet, size=64)) for _ in range(rows)]
        df = pd.DataFrame(
            {
                "id": np.arange(rows, dtype=np.int64),
                "value": np.random.random(rows),
                "payload": payload,
            }
        )
        df.to_parquet(path, engine="pyarrow")
        size = path.stat().st_size
        if size >= target_bytes:
            print(f"Generated {path.name}: {size/1024:.1f} KiB (rows={rows})")
            break
        rows *= 2

generate_parquet(state_dir / "inline-small.parquet", int(0.5 * 1024 * 1024))
generate_parquet(state_dir / "inline-large.parquet", int(1.3 * 1024 * 1024))
EOF
    else
        echo "Skipping inline parquet generation (missing pandas/numpy/pyarrow)."
    fi
fi

if [[ ! -f "$FALLBACK_PARQUET" && -f "$INLINE_SMALL" ]]; then
    cp "$INLINE_SMALL" "$FALLBACK_PARQUET"
    echo "Copied inline-small.parquet to sales_data_10k.parquet fallback."
fi

if [[ ! -f "$INLINE_SMALL" && -f "$FALLBACK_PARQUET" ]]; then
    cp "$FALLBACK_PARQUET" "$INLINE_SMALL"
    echo "Copied fallback parquet to inline-small.parquet."
fi
if [[ ! -f "$INLINE_LARGE" && -f "$FALLBACK_PARQUET" ]]; then
    cp "$FALLBACK_PARQUET" "$INLINE_LARGE"
    echo "Copied fallback parquet to inline-large.parquet."
fi

wait_for_port() {
    local name="$1"
    local host="$2"
    local port="$3"
    local timeout="${4:-150}"
    "$PYTHON_BIN" - "$name" "$host" "$port" "$timeout" <<'PY'
import socket
import sys
import time

name, host, port, timeout = sys.argv[1:5]
port = int(port)
dealine = time.time() + float(timeout)
while time.time() < dealine:
    try:
        with socket.create_connection((host, port), timeout=2):
            print(f"{name}: {host}:{port} ready")
            sys.exit(0)
    except OSError:
        time.sleep(2)
print(f"Timed out waiting for {name} at {host}:{port}", file=sys.stderr)
sys.exit(1)
PY
}

compose_exec() {
    docker compose -f "$COMPOSE_FILE" exec -T "$@"
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

ensure_docker_runtime

MINIO_HOST=$("$PYTHON_BIN" - <<'PY'
from urllib.parse import urlparse
import os
parsed = urlparse(os.environ.get('MINIO_ENDPOINT', 'http://localhost:19100'))
print(parsed.hostname)
PY
)
MINIO_PORT=$("$PYTHON_BIN" - <<'PY'
from urllib.parse import urlparse
import os
parsed = urlparse(os.environ.get('MINIO_ENDPOINT', 'http://localhost:19100'))
print(parsed.port or (443 if parsed.scheme == 'https' else 80))
PY
)

KAFKA_HOST="${KAFKA_BOOTSTRAP%%:*}"
KAFKA_PORT="${KAFKA_BOOTSTRAP##*:}"

echo "Waiting for services (MinIO/Postgres/Kafka)..."
wait_for_port "MinIO" "$MINIO_HOST" "$MINIO_PORT"
wait_for_port "Postgres" "$POSTGRES_HOST" "$POSTGRES_PORT"
wait_for_port "Kafka" "$KAFKA_HOST" "$KAFKA_PORT"

echo "Uploading sample objects to MinIO..."
"$PYTHON_BIN" - "$MINIO_ENDPOINT" "$MINIO_BUCKET" "$MINIO_PREFIX" "$STATE_DIR/test-data" <<'PY'
import os
import sys
import boto3
from botocore.exceptions import ClientError

endpoint, bucket, prefix, data_dir = sys.argv[1:5]
client = boto3.client(
    "s3",
    endpoint_url=endpoint,
    aws_access_key_id="minioadmin",
    aws_secret_access_key="minioadmin",
    region_name="us-east-1",
    use_ssl=endpoint.startswith("https"),
)
try:
    client.create_bucket(Bucket=bucket)
except ClientError as exc:
    if exc.response.get("Error", {}).get("Code") not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
        raise
for root, _, files in os.walk(data_dir):
    for name in files:
        local = os.path.join(root, name)
        rel = os.path.relpath(local, data_dir)
        key = f"{prefix}/{rel}".replace("\\", "/")
        client.upload_file(local, bucket, key)
        print(f"Uploaded {key}")
PY

echo "Creating Postgres fixtures..."
compose_exec postgres env PGPASSWORD="$POSTGRES_PASSWORD" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<'SQL'
CREATE TABLE IF NOT EXISTS public.catalog_test_orders (
    order_id SERIAL PRIMARY KEY,
    customer_name TEXT NOT NULL,
    total NUMERIC(10,2) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
TRUNCATE TABLE public.catalog_test_orders;
INSERT INTO public.catalog_test_orders (customer_name, total) VALUES
('Ada Lovelace', 123.45),
('Grace Hopper', 67.89),
('Alan Turing', 250.00);
SQL

echo "Ensuring Kafka topic exists and publishing a marker event..."
compose_exec kafka bash -c "\
/opt/kafka/bin/kafka-topics.sh --create --if-not-exists \
  --topic catalog-test-events \
  --bootstrap-server kafka:9092 >/tmp/topic.log 2>&1 || cat /tmp/topic.log
"
compose_exec kafka bash -c "\
printf '{"event":"order.created","id":1}\n' | \
/opt/kafka/bin/kafka-console-producer.sh --bootstrap-server kafka:9092 --topic catalog-test-events >/tmp/producer.log 2>&1 || cat /tmp/producer.log
"

touch "$SEED_MARKER"
echo "Stack seeded."
