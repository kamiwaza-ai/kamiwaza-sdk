"""Keep the live export checks strict while accepting the version-2 wrapper."""

import copy
import hashlib
import io
import json
import zipfile

import pytest

from kamiwaza_sdk.schemas.skills import SkillLibraryDetailResponse
from tests.integration._skills_snapshot import validated_snapshot_source
from tests.unit.test_skills_service import _detail_payload

pytestmark = pytest.mark.unit


@pytest.fixture
def snapshot_case():
    original = b"the exact original source ZIP bytes"
    values = _detail_payload()
    values.update(
        display_name="Curated name absent from original source",
        content_checksum=hashlib.sha256(original).hexdigest(),
        marking={
            "profile_id": "generic-test",
            "profile_revision": "1",
            "level_id": "team",
            "raw_text": "opaque test marking",
            "attributes": {"extension": {"labels": ["one", "two"]}},
            "future_field": {"nested": [True, {"value": 7}]},
        },
        metadata={"tags": ["pdf", "report"], "custom": {"nested": [1, 2]}},
    )
    detail = SkillLibraryDetailResponse.model_validate(values)
    item = {
        key: copy.deepcopy(values[key])
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
    item.update(
        metadata={"custom": {"nested": [1, 2]}},
        original_status=values["status"],
        package_path="skills/pdf-generator.zip",
    )
    manifest = {
        "format": "kamiwaza-skills-library-export",
        "version": 2,
        "items": [item],
        "total": 1,
    }
    return original, detail, manifest


def _archive(manifest, source, extra_entries=()):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(manifest["items"][0]["package_path"], source)
        for name in extra_entries:
            archive.writestr(name, b"unexpected")
    return output.getvalue()


def test_snapshot_preserves_current_metadata_and_full_opaque_marking(snapshot_case):
    original, detail, manifest = snapshot_case
    assert (
        validated_snapshot_source(_archive(manifest, original), original, detail)
        == original
    )


@pytest.mark.parametrize("field", ["format", "version", "total"])
def test_snapshot_rejects_wrong_manifest_contract(snapshot_case, field):
    original, detail, manifest = snapshot_case
    manifest[field] = "wrong"
    with pytest.raises(AssertionError):
        validated_snapshot_source(_archive(manifest, original), original, detail)


@pytest.mark.parametrize(
    "field",
    ["display_name", "category", "trigger", "inputs", "tags", "metadata", "marking"],
)
def test_snapshot_rejects_changed_current_metadata(snapshot_case, field):
    original, detail, manifest = snapshot_case
    manifest["items"][0][field] = "changed"
    with pytest.raises(AssertionError):
        validated_snapshot_source(_archive(manifest, original), original, detail)


@pytest.mark.parametrize("field", ["attributes", "future_field"])
def test_snapshot_rejects_dropped_opaque_marking_fields(snapshot_case, field):
    original, detail, manifest = snapshot_case
    del manifest["items"][0]["marking"][field]
    with pytest.raises(AssertionError):
        validated_snapshot_source(_archive(manifest, original), original, detail)


@pytest.mark.parametrize("field", ["marking", "metadata", "content_checksum"])
def test_snapshot_rejects_missing_required_metadata(snapshot_case, field):
    original, detail, manifest = snapshot_case
    del manifest["items"][0][field]
    with pytest.raises(KeyError):
        validated_snapshot_source(_archive(manifest, original), original, detail)


def test_snapshot_rejects_corrupt_inner_source(snapshot_case):
    original, detail, manifest = snapshot_case
    with pytest.raises(AssertionError):
        validated_snapshot_source(_archive(manifest, b"corrupt"), original, detail)


def test_snapshot_rejects_changed_source_even_with_matching_checksums(snapshot_case):
    original, detail, manifest = snapshot_case
    digest = hashlib.sha256(b"replacement").hexdigest()
    manifest["items"][0]["content_checksum"] = digest
    detail = detail.model_copy(update={"content_checksum": digest})
    with pytest.raises(AssertionError, match="differs from the imported bytes"):
        validated_snapshot_source(_archive(manifest, b"replacement"), original, detail)


@pytest.mark.parametrize("extra", ["extra.txt", "skills/another.zip"])
def test_snapshot_rejects_unlisted_entries(snapshot_case, extra):
    original, detail, manifest = snapshot_case
    with pytest.raises(AssertionError):
        validated_snapshot_source(
            _archive(manifest, original, [extra]), original, detail
        )


def test_snapshot_rejects_multiple_manifest_items(snapshot_case):
    original, detail, manifest = snapshot_case
    manifest["items"].append(copy.deepcopy(manifest["items"][0]))
    with pytest.raises(AssertionError):
        validated_snapshot_source(_archive(manifest, original), original, detail)
