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

from kamiwaza_extensions.release_versions import release_identity, release_order

GENERATION = "compat-v1"
WRITER_CAPABILITY = "compat-v1-cas"
IMMUTABLE_CAPABILITY = "compat-v1-immutable"
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


def validate_entry(entry: dict[str, Any], *, allow_legacy: bool = False) -> None:
    """Reject ambiguous identity and constraints unsupported by Core readers."""
    if not isinstance(entry, dict):
        raise ValueError("compat-v1 entry must be a JSON object")
    if not isinstance(entry.get("name"), str) or not entry["name"].strip():
        raise ValueError("compat-v1 entry requires a nonempty name")
    release_identity(entry.get("version"), allow_legacy=allow_legacy)
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
    """Append an immutable release; force never permits rewriting its content."""
    matches = matching_releases(entry, existing)
    if matches:
        assert_immutable_content(entry, matches)
        return deepcopy(existing), "unchanged"
    result = [*deepcopy(existing), deepcopy(entry)]
    result.sort(key=lambda row: (row["name"], release_order(row["version"], allow_legacy=True), row["version"]))
    return result, "insert"


def matching_releases(entry: dict, existing: list[dict]) -> list[dict]:
    """Validate the whole catalog before returning normalized identity matches."""
    validate_entry(entry)
    version = release_identity(entry["version"])
    matches = []
    for row in existing:
        validate_entry(row, allow_legacy=True)
        if row["name"] == entry["name"] and release_identity(row["version"], allow_legacy=True) == version:
            matches.append(row)
    return matches


def assert_immutable_content(entry: dict, matches: list[dict]) -> None:
    """Accept exact repeats only, including equivalent version spellings."""
    expected = _release_content(entry)
    if any(_release_content(row) != expected for row in matches):
        raise ValueError(
            f"Release {entry['name']} {release_identity(entry['version'])} already exists with different content; "
            "published releases are immutable, including with --force. Publish a new version."
        )


def _release_content(entry: dict) -> str:
    # Equivalent version spellings identify the same release. Every other field,
    # including revision, constraints and release notes, is immutable content.
    content = {**entry, "version": release_identity(entry["version"], allow_legacy=True)}
    return json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


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
    return {**entry, "preview_image": f"images/{name}"}, [name]


def publish_compat(publisher: Any, entry: dict, options: dict) -> Any:
    """Bounded optimistic transaction; storage must enforce S3 conditional puts."""
    from kamiwaza_extensions.catalog_publisher import CatalogPublishError, PublishResult

    from kamiwaza_extensions.immutable_release import validate_artifacts

    require_conditional_writes()
    validate_entry(entry)
    validate_artifacts(entry)
    images: list[str] = []
    entry, images = _preview(publisher, entry, options["preview_image_path"])
    uploaded = False
    for _attempt in range(5):
        existing, etag = _read(publisher, options["key"])
        merged, action = merge_release(entry, existing, options["force"])
        if not options["dry_run"] and action != "unchanged":
            if images and not uploaded:
                publisher._upload_preview_image(options["preview_image_path"], images[0])
                uploaded = True
            if not _commit(publisher, options["key"], merged, etag):
                continue
        return PublishResult(
            extension_name=entry["name"],
            version=entry["version"],
            action=action,
            registry_url=publisher._profile.registry,
            catalog_file=options["key"],
            images_pushed=images if uploaded else [],
            dry_run=options["dry_run"],
        )
    raise CatalogPublishError(
        "compat-v1 catalog changed during all 5 commit attempts; retry publication"
    )
