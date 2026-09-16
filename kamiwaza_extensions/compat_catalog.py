"""Explicit, history-preserving catalog generation and conditional S3 commits.

compat-v1 deliberately never uses legacy advisory locks or backup restoration.
The catalog object itself is the concurrency boundary: every write is conditional
on the ETag read for that merge (including conditional first creation).
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from packaging.version import Version

GENERATION = "compat-v1"
WRITER_CAPABILITY = "compat-v1-cas"
_CLAUSE = re.compile(r"(?:>=|<=|==|!=|>|<)?\d+\.\d+(?:\.\d+)?")


def supports_conditional_writes() -> bool:
    """Inspect bundled operation shapes without creating a client or reading credentials."""
    try:
        from botocore.session import Session
    except ImportError:
        return False
    model = Session().get_service_model("s3").operation_model("PutObject")
    return {"IfMatch", "IfNoneMatch"}.issubset(model.input_shape.members)


def require_conditional_writes() -> None:
    if not supports_conditional_writes():
        raise ValueError(
            "compat-v1 requires boto3/botocore >=1.35.70 with conditional S3 PutObject support; "
            "upgrade kamiwaza-sdk[publish] before publishing"
        )


def release_version(value: Any) -> Version:
    """Use the same PEP 440 ordering and normalized identity as Core selection."""
    if not isinstance(value, str):
        raise ValueError("compat-v1 extension version must be a string")
    return Version(value)


def validate_entry(entry: dict[str, Any]) -> None:
    """Reject ambiguous identity and constraints unsupported by Core readers."""
    if not isinstance(entry.get("name"), str) or not entry["name"].strip():
        raise ValueError("compat-v1 entry requires a nonempty name")
    release_version(entry.get("version"))
    validate_constraint(entry.get("kamiwaza_version"))


def validate_constraint(constraint: Any) -> None:
    """Validate the Core numeric constraint grammar (not general PEP 440 ranges)."""
    if constraint is None:
        return
    if not isinstance(constraint, str):
        raise ValueError("kamiwaza_version must be a string")
    if constraint.strip() in {"", "*"}:
        return
    if not all(_CLAUSE.fullmatch(part.strip()) for part in constraint.split(",")):
        raise ValueError(
            "kamiwaza_version requires comma-separated numeric comparisons"
        )


def merge_release(
    entry: dict, existing: list[dict], force: bool = False
) -> tuple[list[dict], str]:
    """Upsert exactly one normalized (name, version), preserving sibling releases."""
    validate_entry(entry)
    version = release_version(entry["version"])
    matches = []
    for index, row in enumerate(existing):
        validate_entry(row)
        if row["name"] == entry["name"] and release_version(row["version"]) == version:
            matches.append(index)
    if matches and not force:
        raise ValueError(
            f"Release {entry['name']} {version} already exists; use --force for this release only"
        )
    result = [
        deepcopy(row) for index, row in enumerate(existing) if index not in matches
    ]
    result.append(deepcopy(entry))
    result.sort(key=lambda row: (row["name"], release_version(row["version"])))
    return result, "replace" if matches else "insert"


def _read(publisher: Any, key: str) -> tuple[list[dict], str | None]:
    try:
        response = publisher._s3.get_object(
            Bucket=publisher._profile.catalog_bucket, Key=key
        )
    except publisher._ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "NoSuchKey":
            return [], None
        raise
    entries = json.loads(response["Body"].read())
    if not isinstance(entries, list):
        raise ValueError("compat-v1 catalog must be a JSON array")
    etag = response.get("ETag")
    if not etag:
        raise ValueError(
            "Storage did not return an ETag; refusing unconditional publication"
        )
    return entries, etag


def _put(publisher: Any, key: str, merged: list[dict], etag: str | None) -> None:
    condition = {"IfMatch": etag} if etag else {"IfNoneMatch": "*"}
    publisher._s3.put_object(
        Bucket=publisher._profile.catalog_bucket,
        Key=key,
        Body=json.dumps(merged, indent=2, ensure_ascii=False).encode(),
        ContentType="application/json",
        Metadata={"writer-capability": WRITER_CAPABILITY},
        **condition,
    )


def _commit(publisher: Any, key: str, merged: list[dict], etag: str | None) -> bool:
    """Return false only for a concurrency conflict; other failures are terminal."""
    try:
        _put(publisher, key, merged, etag)
    except publisher._ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {
            "PreconditionFailed",
            "ConditionalRequestConflict",
            "412",
            "409",
        }:
            return False
        raise
    return True


def _preview(publisher: Any, entry: dict, path: Path | None) -> tuple[dict, list[str]]:
    if path is None:
        return entry, []
    # Content-addressing prevents a failed publish from changing a prior release's
    # preview. An orphan image is safe; rolling back a concurrent catalog is not.
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    name = f"{digest}{path.suffix.lower()}"
    publisher._upload_preview_image(path, name)
    return {**entry, "preview_image": f"images/{name}"}, [name]


def publish_compat(publisher: Any, entry: dict, options: dict) -> Any:
    """Bounded optimistic transaction; storage must enforce S3 conditional puts."""
    from kamiwaza_extensions.catalog_publisher import CatalogPublishError, PublishResult

    require_conditional_writes()
    validate_entry(entry)
    images: list[str] = []
    if not options["dry_run"]:
        entry, images = _preview(publisher, entry, options["preview_image_path"])
    for _attempt in range(5):
        existing, etag = _read(publisher, options["key"])
        merged, action = merge_release(entry, existing, options["force"])
        if not options["dry_run"]:
            if not _commit(publisher, options["key"], merged, etag):
                continue
        return PublishResult(
            extension_name=entry["name"],
            version=entry["version"],
            action=action,
            registry_url=publisher._profile.registry,
            catalog_file=options["key"],
            images_pushed=images,
            dry_run=options["dry_run"],
        )
    raise CatalogPublishError(
        "compat-v1 catalog changed during all 5 commit attempts; retry publication"
    )
