"""Assertions for the Skills Library's version-2 export contract."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

from kamiwaza_sdk.schemas.skills import SkillLibraryDetailResponse


def snapshot_metadata(detail: SkillLibraryDetailResponse) -> dict:
    """Expected mutable metadata, including the entire opaque marking envelope."""
    values = detail.model_dump(mode="json")
    expected = {
        key: values[key]
        for key in (
            "name",
            "display_name",
            "category",
            "trigger",
            "inputs",
            "marking",
            "tags",
            "content_checksum",
        )
    }
    expected["metadata"] = {
        key: value
        for key, value in (values["metadata"] or {}).items()
        if key not in {"package_manifest", "tags"}
    }
    return expected


def validated_snapshot_source(
    snapshot: bytes, original: bytes, detail: SkillLibraryDetailResponse
) -> bytes:
    """Validate the single-item snapshot before returning its original source ZIP."""
    with zipfile.ZipFile(io.BytesIO(snapshot)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == "kamiwaza-skills-library-export"
        assert manifest["version"] == 2
        assert manifest["total"] == 1
        assert len(manifest["items"]) == 1
        item = manifest["items"][0]
        package_path = item["package_path"]
        assert package_path.startswith("skills/")
        assert package_path.endswith(".zip")
        assert sorted(archive.namelist()) == sorted(["manifest.json", package_path])
        expected = snapshot_metadata(detail)
        assert {key: item[key] for key in expected} == expected
        assert item["original_status"] == detail.status
        source = archive.read(package_path)
    assert hashlib.sha256(source).hexdigest() == item["content_checksum"]
    assert source == original, "snapshot source differs from the imported bytes"
    return source
