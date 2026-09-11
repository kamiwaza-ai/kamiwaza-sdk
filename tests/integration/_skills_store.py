"""Direct access to the Skills Library backing stores (ENG-11524).

The Skills Library persists an imported skill in **two** places:

* a row in the ``skill_library`` table, and
* a package artifact in object storage under that row's ``storage_path``.

``storage_path`` is deliberately not exposed by the API, so a test that only
calls ``/skills`` cannot locate the artifact, and cannot distinguish the soft
delete the capability document claims from a hard one — ``DELETE`` reports
success either way and subsequent reads 404 either way.

This module supplies read-only access to both stores. It holds no knowledge of
how the platform is deployed: the caller supplies a DSN and object-store
settings through the environment, the same way the two-cluster federation tests
take ``KAMIWAZA_PEER_BASE_URL``. Obtaining those (port-forward, bastion, local
compose) is the operator's problem, not the test's.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cache

DSN_ENV = "KAMIWAZA_SKILLS_STORE_DSN"
S3_ENDPOINT_ENV = "KAMIWAZA_SKILLS_STORE_S3_ENDPOINT_URL"
S3_BUCKET_ENV = "KAMIWAZA_SKILLS_STORE_S3_BUCKET"
S3_ACCESS_KEY_ENV = "KAMIWAZA_SKILLS_STORE_S3_ACCESS_KEY_ID"
S3_SECRET_KEY_ENV = "KAMIWAZA_SKILLS_STORE_S3_SECRET_ACCESS_KEY"
S3_REGION_ENV = "KAMIWAZA_SKILLS_STORE_S3_REGION"

_REQUIRED_ENV = (
    DSN_ENV,
    S3_ENDPOINT_ENV,
    S3_BUCKET_ENV,
    S3_ACCESS_KEY_ENV,
    S3_SECRET_KEY_ENV,
)


@dataclass(frozen=True)
class SkillRow:
    """The persisted record, as the database actually holds it."""

    id: str
    name: str
    status: str
    storage_path: str
    content_checksum: str
    deleted_at: object | None

    @property
    def is_soft_deleted(self) -> bool:
        return self.deleted_at is not None


@dataclass(frozen=True)
class StoreConfig:
    """Connection settings for both backing stores."""

    dsn: str
    s3_endpoint_url: str
    s3_bucket: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_region: str


def missing_store_env() -> list[str]:
    """Names of the required settings that are absent or empty.

    Returns an empty list when the stores are fully configured. Callers use
    this to skip rather than fail: a host without store access is a normal
    under-provisioned host, not a defect in the platform.
    """

    return [name for name in _REQUIRED_ENV if not os.environ.get(name, "").strip()]


def load_store_config() -> StoreConfig:
    """Build the store configuration from the environment.

    Raises ``RuntimeError`` when a required setting is absent. Callers are
    expected to have consulted :func:`missing_store_env` first; reaching here
    unconfigured is a programming error, not an environment condition.
    """

    missing = missing_store_env()
    if missing:
        raise RuntimeError(
            "Skills Library store access is not configured; missing: "
            + ", ".join(missing)
        )

    return StoreConfig(
        dsn=os.environ[DSN_ENV].strip(),
        s3_endpoint_url=os.environ[S3_ENDPOINT_ENV].strip(),
        s3_bucket=os.environ[S3_BUCKET_ENV].strip(),
        s3_access_key_id=os.environ[S3_ACCESS_KEY_ENV].strip(),
        s3_secret_access_key=os.environ[S3_SECRET_KEY_ENV].strip(),
        s3_region=os.environ.get(S3_REGION_ENV, "").strip() or "us-east-1",
    )


def fetch_skill_row(config: StoreConfig, skill_id: str) -> SkillRow | None:
    """Read one ``skill_library`` row by id, ignoring the soft-delete filter.

    Returns ``None`` only when no row exists at all. A soft-deleted row is
    returned with ``deleted_at`` populated — distinguishing "retained but
    hidden" from "destroyed" is the whole point of reading the store.
    """

    import psycopg

    query = """
        SELECT id, name, status, storage_path, content_checksum, deleted_at
        FROM skill_library
        WHERE id = %s
    """
    # libpq waits indefinitely by default, so a port-forward that died mid-run
    # would hang the live lane rather than failing it.
    with (
        psycopg.connect(config.dsn, connect_timeout=10) as conn,
        conn.cursor() as cur,
    ):
        cur.execute(query, (skill_id,))
        record = cur.fetchone()

    if record is None:
        return None

    # Coerced, not merely annotated: the driver decides what a column comes
    # back as, and an unenforced ``: str`` is a claim rather than a guarantee.
    return SkillRow(
        id=str(record[0]),
        name=str(record[1]),
        status=str(record[2]),
        storage_path=str(record[3]),
        content_checksum=str(record[4]),
        deleted_at=record[5],
    )


@cache
def _s3_client(config: StoreConfig):
    """One client per configuration — the two readers below share it."""

    import boto3  # type: ignore[import-untyped]  # no stubs; dev-group dep

    return boto3.client(
        "s3",
        endpoint_url=config.s3_endpoint_url,
        aws_access_key_id=config.s3_access_key_id,
        aws_secret_access_key=config.s3_secret_access_key,
        region_name=config.s3_region,
    )


def list_package_objects(config: StoreConfig, storage_path: str) -> list[str]:
    """Object keys stored under a row's ``storage_path``."""

    prefix = storage_path if storage_path.endswith("/") else storage_path + "/"
    client = _s3_client(config)
    response = client.list_objects_v2(Bucket=config.s3_bucket, Prefix=prefix)
    return [item["Key"] for item in response.get("Contents", [])]


def read_package_bytes(config: StoreConfig, storage_path: str) -> bytes:
    """The stored ``package.zip`` bytes for a row's ``storage_path``."""

    prefix = storage_path if storage_path.endswith("/") else storage_path + "/"
    client = _s3_client(config)
    response = client.get_object(Bucket=config.s3_bucket, Key=prefix + "package.zip")
    return response["Body"].read()
