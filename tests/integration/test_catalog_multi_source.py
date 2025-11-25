from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Iterable
from uuid import uuid4

import pytest

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.catalog import ContainerCreate

pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]

_MINIO_CREDS = {
    "aws_access_key_id": "minioadmin",
    "aws_secret_access_key": "minioadmin",
}


def _cleanup_datasets(client, urns: Iterable[str]) -> None:
    seen: set[str] = set()
    for urn in urns:
        if not urn or urn in seen:
            continue
        try:
            client.delete("/catalog/datasets/by-urn", params={"urn": urn})
        except APIError:
            pass
        seen.add(urn)


def _fetch_dataset(client, urn: str) -> Dict:
    return client.get("/catalog/datasets/by-urn", params={"urn": urn})


def _ensure_retrieval_metadata(client, urn: str, endpoint: str) -> None:
    dataset = _fetch_dataset(client, urn)
    props = dict(dataset.get("properties") or {})
    props.setdefault("endpoint", endpoint)
    props.setdefault("endpoint_url", endpoint)
    props.setdefault("endpoint_override", endpoint)
    props.setdefault("region", props.get("region", "us-east-1"))
    client.patch(
        "/catalog/datasets/by-urn",
        params={"urn": urn},
        json={"properties": props},
    )


def _run_inline_retrieval(client, dataset_urn: str, *, format_hint: str, endpoint: str) -> Dict:
    payload = {
        "dataset_urn": dataset_urn,
        "transport": "inline",
        "format_hint": format_hint,
        "credential_override": json.dumps(
            {
                **_MINIO_CREDS,
                "endpoint": endpoint,
                "endpoint_override": endpoint,
                "endpoint_url": endpoint,
                "region": "us-east-1",
            }
        ),
    }
    job = client.post("/retrieval/jobs", json=payload)
    assert job["transport"] == "inline"
    inline = job.get("inline")
    assert inline is not None and inline["row_count"] > 0
    return inline


def _run_sse_retrieval(client, dataset_urn: str, *, format_hint: str, endpoint: str) -> None:
    payload = {
        "dataset_urn": dataset_urn,
        "transport": "sse",
        "format_hint": format_hint,
        "credential_override": json.dumps(
            {
                **_MINIO_CREDS,
                "endpoint": endpoint,
                "endpoint_override": endpoint,
                "endpoint_url": endpoint,
                "region": "us-east-1",
            }
        ),
    }
    job = client.post("/retrieval/jobs", json=payload)
    assert job["transport"] == "sse"
    job_id = job["job_id"]
    response = client.get(
        f"/retrieval/jobs/{job_id}/stream",
        expect_json=False,
        stream=True,
    )
    try:
        events = []
        for raw in response.iter_lines():
            if raw is None:
                continue
            line = raw.decode("utf-8")
            if line.startswith("data:"):
                events.append(line)
                if len(events) >= 1:
                    break
        assert events, "SSE stream did not emit any events"
    finally:
        response.close()


def _ingest_object_dataset(
    client,
    *,
    bucket: str,
    key: str,
    endpoint: str,
    region: str,
) -> tuple[str, list[str]]:
    response = client.ingestion.run_active(
        "s3",
        bucket=bucket,
        prefix=key,
        endpoint_url=endpoint,
        region=region,
        **_MINIO_CREDS,
    )
    dataset_urns = response.urns
    if not dataset_urns:
        dataset_urns = [f"urn:li:dataset:(urn:li:dataPlatform:s3,{bucket}/{key},PROD)"]
    target = dataset_urns[0]
    _ensure_retrieval_metadata(client, target, endpoint)
    return target, dataset_urns


def test_catalog_file_ingestion_metadata(live_kamiwaza_client, catalog_stack_environment):
    server_root_candidates = [
        Path.home() / "code" / "kamiwaza",
        Path.home() / "kamiwaza",
    ]
    target_root = None
    for candidate in server_root_candidates:
        candidate_path = candidate / "tests" / "integration" / "services" / "ingestion" / "docker" / "state" / "test-data"
        if candidate_path.exists():
            target_root = candidate_path
            break

    if target_root is None:
        checked = ", ".join(str((c / 'tests/integration/services/ingestion/docker/state/test-data').resolve()) for c in server_root_candidates)
        pytest.xfail(
            f"Ingestion folders for file ingestion do not exist; checked: {checked}"
        )

    file_root = str(target_root)
    dataset_urns: list[str] = []
    try:
        response = live_kamiwaza_client.ingestion.run_active(
            "file",
            path=file_root,
            recursive=True,
        )
        dataset_urns = response.urns
        assert dataset_urns, "file ingestion did not return dataset URNs"
        target_urn = None
        format_hint = None
        for urn in dataset_urns:
            dataset = _fetch_dataset(live_kamiwaza_client, urn)
            assert dataset["platform"] == "file"
            path = (dataset.get("properties") or {}).get("path", "") or ""
            lowered = path.lower()
            if lowered.endswith(".parquet"):
                target_urn = urn
                format_hint = "parquet"
                break
            if lowered.endswith(".json"):
                target_urn = urn
                format_hint = "json"
                break
            if lowered.endswith(".csv"):
                target_urn = urn
                format_hint = "csv"
                break

        if not target_urn:
            pytest.skip("File ingester did not produce a dataset with a supported file extension")

        target_dataset = _fetch_dataset(live_kamiwaza_client, target_urn)
        props = dict(target_dataset.get("properties") or {})
        if props.get("location") is None and props.get("path"):
            props["location"] = props["path"]
            live_kamiwaza_client.patch(
                "/catalog/datasets/by-urn",
                params={"urn": target_urn},
                json={"properties": props},
            )

        try:
            job = live_kamiwaza_client.post(
                "/retrieval/jobs",
                json={
                    "dataset_urn": target_urn,
                    "transport": "inline",
                    "format_hint": format_hint or "parquet",
                },
            )
        except APIError as exc:
            detail = (exc.response_text or "").lower()
            if exc.status_code == 400 and "filesystem datasets are disabled" in detail:
                pytest.skip(
                    "Filesystem retrieval disabled on server (set RETRIEVAL_FILESYSTEM_ALLOWED_ROOTS to enable)"
                )
            raise

        inline = job.get("inline")
        assert inline is not None, "Inline retrieval did not return payload"
        assert inline["row_count"] >= 1
    finally:
        _cleanup_datasets(live_kamiwaza_client, dataset_urns)


def test_catalog_object_ingestion_inline_retrieval(live_kamiwaza_client, catalog_stack_environment):
    cfg = catalog_stack_environment["object"]
    dataset_urns: list[str] = []
    prefix = f"{cfg['prefix']}/objects"
    try:
        response = live_kamiwaza_client.ingestion.run_active(
            "s3",
            bucket=cfg["bucket"],
            prefix=prefix,
            endpoint_url=cfg["endpoint"],
            region=cfg["region"],
            **_MINIO_CREDS,
        )
        dataset_urns = response.urns
        assert dataset_urns, "S3 ingestion returned no datasets"
        target = next((urn for urn in dataset_urns if "sample.json" in urn), dataset_urns[0])
        _ensure_retrieval_metadata(live_kamiwaza_client, target, cfg["endpoint"])
        try:
            inline = _run_inline_retrieval(
                live_kamiwaza_client,
                target,
                format_hint="json",
                endpoint=cfg["endpoint"],
            )
        except APIError:
            pytest.xfail(
                "Inline retrieval for JSON objects still fails; "
                "see docs-local/00-server-defects.md#object-json-retrieval"
            )
        assert isinstance(inline["data"], list)
    finally:
        _cleanup_datasets(live_kamiwaza_client, dataset_urns)


def test_catalog_parquet_ingestion_inline_retrieval(live_kamiwaza_client, catalog_stack_environment):
    cfg = catalog_stack_environment["object"]
    dataset_urns: list[str] = []
    prefix = f"{cfg['prefix']}/sales_data_10k.parquet"
    try:
        response = live_kamiwaza_client.ingestion.run_active(
            "s3",
            bucket=cfg["bucket"],
            prefix=prefix,
            endpoint_url=cfg["endpoint"],
            region=cfg["region"],
            **_MINIO_CREDS,
        )
        dataset_urns = response.urns
        assert dataset_urns, "Parquet ingestion returned no datasets"
        target = next((urn for urn in dataset_urns if "sales_data_10k" in urn), dataset_urns[0])
        _ensure_retrieval_metadata(live_kamiwaza_client, target, cfg["endpoint"])
        inline = _run_inline_retrieval(
            live_kamiwaza_client,
            target,
            format_hint="parquet",
            endpoint=cfg["endpoint"],
        )
        assert inline["row_count"] > 0
    finally:
        _cleanup_datasets(live_kamiwaza_client, dataset_urns)


def test_catalog_inline_small_object_succeeds(live_kamiwaza_client, catalog_stack_environment):
    cfg = catalog_stack_environment["object"]
    dataset_urns: list[str] = []
    try:
        target, dataset_urns = _ingest_object_dataset(
            live_kamiwaza_client,
            bucket=cfg["bucket"],
            key=cfg["small_key"],
            endpoint=cfg["endpoint"],
            region=cfg["region"],
        )
        inline = _run_inline_retrieval(
            live_kamiwaza_client,
            target,
            format_hint="parquet",
            endpoint=cfg["endpoint"],
        )
        assert inline["row_count"] > 0
    finally:
        _cleanup_datasets(live_kamiwaza_client, dataset_urns)


def test_catalog_inline_large_object_hits_threshold(live_kamiwaza_client, catalog_stack_environment):
    cfg = catalog_stack_environment["object"]
    dataset_urns: list[str] = []
    try:
        target, dataset_urns = _ingest_object_dataset(
            live_kamiwaza_client,
            bucket=cfg["bucket"],
            key=cfg["large_key"],
            endpoint=cfg["endpoint"],
            region=cfg["region"],
        )
        with pytest.raises(APIError) as excinfo:
            _run_inline_retrieval(
                live_kamiwaza_client,
                target,
                format_hint="parquet",
                endpoint=cfg["endpoint"],
            )
        assert excinfo.value.status_code == 422
        detail = (excinfo.value.response_text or "").lower()
        assert "inline threshold" in detail
    finally:
        _cleanup_datasets(live_kamiwaza_client, dataset_urns)


def test_catalog_large_object_sse_retrieval(live_kamiwaza_client, catalog_stack_environment):
    cfg = catalog_stack_environment["object"]
    dataset_urns: list[str] = []
    try:
        target, dataset_urns = _ingest_object_dataset(
            live_kamiwaza_client,
            bucket=cfg["bucket"],
            key=cfg["large_key"],
            endpoint=cfg["endpoint"],
            region=cfg["region"],
        )
        _run_sse_retrieval(
            live_kamiwaza_client,
            target,
            format_hint="parquet",
            endpoint=cfg["endpoint"],
        )
    finally:
        _cleanup_datasets(live_kamiwaza_client, dataset_urns)


def test_catalog_container_membership_round_trip(live_kamiwaza_client, catalog_stack_environment):
    cfg = catalog_stack_environment["object"]
    dataset_urns: list[str] = []
    container_urn: str | None = None
    try:
        target, dataset_urns = _ingest_object_dataset(
            live_kamiwaza_client,
            bucket=cfg["bucket"],
            key=cfg["small_key"],
            endpoint=cfg["endpoint"],
            region=cfg["region"],
        )
        containers = live_kamiwaza_client.catalog.containers
        container_payload = ContainerCreate(
            name=f"sdk-catalog-{uuid4().hex[:8]}",
            platform="integration",
        )
        container_urn = containers.create(container_payload)
        containers.add_dataset(container_urn, target)
        container = containers.get(container_urn)
        assert target in container.datasets
        containers.remove_dataset(container_urn, target)
        refreshed = containers.get(container_urn)
        assert target not in refreshed.datasets
    finally:
        if container_urn:
            try:
                live_kamiwaza_client.catalog.containers.delete(container_urn)
            except APIError:
                pass
        _cleanup_datasets(live_kamiwaza_client, dataset_urns)


def test_catalog_postgres_ingestion_metadata(live_kamiwaza_client, catalog_stack_environment):
    pg = catalog_stack_environment["postgres"]
    dataset_urns: list[str] = []
    try:
        response = live_kamiwaza_client.ingestion.run_active(
            "postgres",
            host=pg["host"],
            port=pg["port"],
            database=pg["database"],
            user=pg["user"],
            password=pg["password"],
            schema=pg["schema"],
        )
        dataset_urns = response.urns
        assert dataset_urns, "Postgres ingestion returned no datasets"
        orders = next((urn for urn in dataset_urns if "catalog_test_orders" in urn), dataset_urns[0])
        dataset = _fetch_dataset(live_kamiwaza_client, orders)
        assert dataset["platform"] == "postgres"
        job = live_kamiwaza_client.post(
            "/retrieval/jobs",
            json={
                "dataset_urn": orders,
                "transport": "inline",
                "format_hint": "parquet",
            },
        )
        assert job["transport"] == "inline"
        assert job["inline"]["row_count"] >= 1
    finally:
        _cleanup_datasets(live_kamiwaza_client, dataset_urns)


def test_catalog_kafka_ingestion_metadata(live_kamiwaza_client, catalog_stack_environment):
    kafka = catalog_stack_environment["kafka"]
    dataset_urns: list[str] = []
    try:
        response = live_kamiwaza_client.ingestion.run_active(
            "kafka",
            bootstrap_servers=kafka["bootstrap"],
        )
        dataset_urns = response.urns
        assert dataset_urns, "Kafka ingestion did not return datasets"
        topic = next((urn for urn in dataset_urns if kafka["topic"] in urn), dataset_urns[0])
        dataset = _fetch_dataset(live_kamiwaza_client, topic)
        assert dataset["platform"] == "kafka"
        pytest.xfail(
            "Kafka datasets expose metadata only; retrieval transport is not implemented yet; "
            "see docs-local/00-server-defects.md#kafka-retrieval-missing"
        )
    finally:
        _cleanup_datasets(live_kamiwaza_client, dataset_urns)


def test_catalog_slack_ingestion_metadata(live_kamiwaza_client):
    token = os.environ.get("SLACK_TEST_TOKEN")
    channel = os.environ.get("SLACK_TEST_CHANNEL")
    multi_channels = os.environ.get("SLACK_TEST_CHANNELS")
    team_id = os.environ.get("SLACK_TEST_TEAM")
    if not (token and channel and team_id):
        pytest.skip("Provide SLACK_TEST_TOKEN/SLACK_TEST_CHANNEL/SLACK_TEST_TEAM to exercise Slack ingestion")

    channel_list: list[str] = []
    if multi_channels:
        channel_list.extend([item.strip() for item in multi_channels.split(",") if item.strip()])
    if not channel_list:
        channel_list = [channel]

    dataset_urns: list[str] = []
    try:
        response = live_kamiwaza_client.ingestion.run_slack_ingest(
            channels=channel_list,
            token=token,
            team_id=team_id,
            max_messages=3,
        )
        dataset_urns = response.urns
        assert dataset_urns, "Slack ingestion returned no datasets"
        dataset = _fetch_dataset(live_kamiwaza_client, dataset_urns[0])
        assert dataset["platform"] == "slack"
        props = dataset.get("properties") or {}
        resolved_channel = props.get("channel_id") or channel_list[0]

        rows = live_kamiwaza_client.retrieval.slack_messages(
            dataset_urns[0],
            channels=[resolved_channel],
            max_messages=3,
            include_replies=False,
        )

        assert rows, "Slack retrieval did not return inline payload"
        for row in rows[:5]:
            ts = row.get("ts") or row.get("timestamp")
            user = row.get("user") or row.get("username")
            text = row.get("text") or row.get("message")
            print(f"[slack-row] ts={ts} user={user} text={text}")
    finally:
        _cleanup_datasets(live_kamiwaza_client, dataset_urns)
