from __future__ import annotations

import io
import time
from typing import Iterable

import boto3
import pandas as pd

MINIO_ENDPOINT = "http://localhost:9100"
ACCESS_KEY = "minioadmin"
SECRET_KEY = "minioadmin"
REGION = "us-east-1"
BUCKET = "kamiwaza-sdk-tests"
PREFIX = "sdk-integration"


def wait_for_minio(timeout: float = 60.0) -> None:
    """Poll the MinIO health endpoint until it responds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            client = boto3.client(
                "s3",
                endpoint_url=MINIO_ENDPOINT,
                aws_access_key_id=ACCESS_KEY,
                aws_secret_access_key=SECRET_KEY,
                region_name=REGION,
                use_ssl=False,
            )
            client.list_buckets()
            return
        except Exception:  # pragma: no cover - best effort wait loop
            time.sleep(2)
    raise RuntimeError("MinIO did not become ready within timeout")


def write_parquet_frame(client, key: str, rows: Iterable[dict]) -> None:
    df = pd.DataFrame(list(rows))
    buffer = io.BytesIO()
    df.to_parquet(buffer, index=False)
    buffer.seek(0)
    client.put_object(Bucket=BUCKET, Key=key, Body=buffer.getvalue())


def main() -> None:
    wait_for_minio()

    client = boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        region_name=REGION,
        use_ssl=False,
    )

    existing = [bucket["Name"] for bucket in client.list_buckets().get("Buckets", [])]
    if BUCKET not in existing:
        client.create_bucket(Bucket=BUCKET)

    dataset_key = f"{PREFIX}/visitors.parquet"
    write_parquet_frame(
        client,
        dataset_key,
        [
            {"visitor_id": 1, "store": "downtown", "spend": 23.50},
            {"visitor_id": 2, "store": "uptown", "spend": 14.10},
            {"visitor_id": 3, "store": "downtown", "spend": 9.99},
        ],
    )


if __name__ == "__main__":
    main()
