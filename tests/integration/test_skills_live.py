"""Live integration tests for the Skills Library SDK service.

ENG-11524. The lifecycle test here is the evidence arm for the
``skills.skills-library`` capability, so two properties matter beyond "does it
pass":

* **It verifies the backing stores directly.** The capability document claims
  deletion is *soft* — "rather than destroying history". The API cannot show
  that: ``DELETE`` returns success and every later read 404s whether the row
  was retained or destroyed. Only the ``skill_library`` row proves it. The API
  also never exposes ``storage_path``, so the package artifact is unreachable
  without reading the row first.

* **The lifecycle and the store checks are one test, deliberately.** The
  evidence emitter drops an entry whose matched tests all skipped, but folds a
  green-with-skips run to ``passed_with_notes``. Split across two tests, a
  skipped store check beside a passing API check would emit a record claiming
  this capability is characterized while the store was never looked at. Fused,
  the test either verifies both halves or emits nothing.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import NoReturn
from uuid import uuid4

import pytest

from kamiwaza_sdk.exceptions import APIError, NotFoundError
from kamiwaza_sdk.schemas.skills import SkillLibraryUpdateRequest
from kamiwaza_sdk.services.skills import SkillsService

from ._skills_store import (
    fetch_skill_row,
    list_package_objects,
    load_store_config,
    missing_store_env,
    read_package_bytes,
)

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def _build_skill_package_bytes(
    *, name: str, display_name: str, description: str
) -> bytes:
    skill_markdown = f"""---
name: {name}
description: {description}
metadata:
  kamiwaza:
    category: export
    tags:
      - integration
      - pdf
---

# {display_name}

Use this skill in integration tests.
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{name}/SKILL.md", skill_markdown)
        archive.writestr(f"{name}/scripts/render.sh", "#!/bin/sh\necho render\n")
    return buffer.getvalue()


def _skills_service_available(service: SkillsService) -> bool:
    try:
        service.list_skills(page_size=1)
        return True
    except APIError as exc:
        if exc.status_code in {404, 503}:
            return False
        raise


def _require_mutation_available(exc: APIError) -> NoReturn:
    """Translate a failed mutation into a skip only where that is honest.

    A ``403`` means this caller was never granted the skills write context —
    an environment provisioning gap, and a legitimate skip.

    A ``404``/``503`` is **not** a skip here. Every caller of this helper has
    already listed skills successfully, so the service is present; an absent or
    unavailable mutation route on a platform whose read route answers is a
    genuine defect in a capability that claims import, curation and deletion.
    The earlier revision of this module skipped on those too, which would have
    reported a half-present Skills Library as a clean run.
    """

    if exc.status_code == 403:
        pytest.skip("Skills mutation paths require operator/admin access")
    raise exc


def _skip_without_store_access() -> None:
    missing = missing_store_env()
    if missing:
        pytest.skip(
            "Direct store verification is not configured; missing: "
            + ", ".join(missing)
        )


@pytest.mark.requires_platform_store
def test_skills_library_lifecycle_and_backing_store(live_kamiwaza_client) -> None:
    """Import, curate, publish, export and soft-delete, checking both stores.

    This is the test mapped to ``skills.skills-library`` in
    ``tests/e2e/capability_map.yaml``.
    """

    service = live_kamiwaza_client.skills
    assert isinstance(service, SkillsService)

    if not _skills_service_available(service):
        pytest.skip("Skills Library endpoints are unavailable in this environment")

    _skip_without_store_access()
    store = load_store_config()

    unique_suffix = uuid4().hex[:8]
    skill_name = f"sdk-skill-{unique_suffix}"
    display_name = f"SDK Skill {unique_suffix}"
    description = "SDK live integration skill"

    package_bytes = _build_skill_package_bytes(
        name=skill_name,
        display_name=display_name,
        description=description,
    )
    package_digest = hashlib.sha256(package_bytes).hexdigest()

    created = None
    try:
        try:
            created = service.import_skill_package(
                filename=f"{skill_name}.zip",
                file_content=package_bytes,
            )
        except APIError as exc:
            _require_mutation_available(exc)

        assert created.name == skill_name
        assert created.status == "draft"
        assert created.package_summary.root_dir == skill_name
        # root_dir falls back to the skill name when package_manifest is
        # missing, and the fixture names the zip root the same thing - so
        # the line above passes even if the manifest was never persisted.
        assert created.package_summary.has_scripts is True
        assert created.content_checksum == package_digest

        # --- the row, as the database actually holds it -------------------
        row = fetch_skill_row(store, str(created.id))
        assert row is not None, (
            "import returned success but no skill_library row exists"
        )
        assert row.name == skill_name
        assert row.status == "draft"
        assert row.deleted_at is None
        assert row.storage_path, (
            "row carries no storage_path, so the package is unreachable"
        )
        # The digest agrees across all three representations: the bytes we
        # uploaded, the row, and (below) the stored artifact.
        assert row.content_checksum == package_digest

        # --- the artifact, read straight out of object storage ------------
        stored_keys = list_package_objects(store, row.storage_path)
        assert any(key.endswith("/package.zip") for key in stored_keys), (
            f"no package.zip under {row.storage_path}; found {stored_keys}"
        )
        stored_package = read_package_bytes(store, row.storage_path)
        assert hashlib.sha256(stored_package).hexdigest() == package_digest
        assert stored_package == package_bytes

        detail = service.get_skill(created.id)
        # Not just the id, which a detail route echoes from the path: pin fields
        # that identify the row behind it.
        assert detail.id == created.id
        assert detail.name == skill_name
        assert detail.content_checksum == package_digest
        assert detail.tags == ["integration", "pdf"]

        # --- curation, without repackaging ------------------------------
        # The capability guarantees metadata is editable in place while the
        # imported package stays read-only. This scenario is named for it, so
        # it has to happen here: edit a curation field, confirm it took, and
        # confirm the package did NOT move underneath it.
        curated_name = f"{display_name} (curated)"
        curated = service.update_skill_metadata(
            created.id,
            SkillLibraryUpdateRequest(display_name=curated_name),
        )
        assert curated.display_name == curated_name
        assert service.get_skill(created.id).display_name == curated_name
        curated_row = fetch_skill_row(store, str(created.id))
        assert curated_row is not None
        assert curated_row.content_checksum == package_digest
        assert curated_row.storage_path == row.storage_path, (
            "curation relocated the package; the guarantee is edit-in-place"
        )
        assert read_package_bytes(store, curated_row.storage_path) == package_bytes

        # --- publication lifecycle, confirmed in the store ----------------
        # The document guarantees `draft` -> `published` -> `archived` and an
        # `archived` -> `draft` restore, in the platform's own vocabulary.
        for target_status in ("published", "archived", "draft"):
            updated = service.update_skill_metadata(
                created.id,
                SkillLibraryUpdateRequest(status=target_status),
            )
            assert updated.status == target_status
            stored_row = fetch_skill_row(store, str(created.id))
            assert stored_row is not None
            assert stored_row.status == target_status, (
                f"API reported {target_status} but the row still reads "
                f"{stored_row.status}"
            )

        # Re-publish so the status="published" listing filter below matches.
        # Confirmed in the store like the transitions above: a PUT that reports
        # `published` while the row stays `draft` would otherwise be invisible
        # at exactly the transition the listing assertion leans on.
        published = service.update_skill_metadata(
            created.id,
            SkillLibraryUpdateRequest(status="published"),
        )
        assert published.status == "published"
        republished_row = fetch_skill_row(store, str(created.id))
        assert republished_row is not None
        assert republished_row.status == "published"

        listing = service.list_skills(
            q=skill_name,
            category="export",
            tag="integration",
            status="published",
            page_size=100,
        )
        matching_ids = {item.id for item in listing.items}
        assert created.id in matching_ids

        # Inclusion alone proves nothing: on a cluster whose first page is
        # short, a server ignoring every filter still returns this skill. So
        # supply ONE filter at a time - a query carrying several cannot say
        # which one excluded - and pair each non-matching value with a
        # matching control. Without the control, a server that returned an
        # empty page for any single-filter query would pass all four.
        for label, miss, hit in (
            ("category", {"category": "analysis"}, {"category": "export"}),
            ("tag", {"tag": "no-such-tag"}, {"tag": "integration"}),
            ("status", {"status": "archived"}, {"status": "published"}),
            ("q", {"q": f"{skill_name}-nomatch"}, {"q": skill_name}),
        ):
            excluded = service.list_skills(page_size=100, **miss)
            assert created.id not in {item.id for item in excluded.items}, (
                f"{label} filter did not exclude a non-matching skill"
            )
            included = service.list_skills(page_size=100, **hit)
            assert created.id in {item.id for item in included.items}, (
                f"{label} filter returned nothing for a matching value, so its "
                "exclusion above proves nothing"
            )

        # Each export is compared against the uploaded bytes, not merely
        # asserted non-empty. A wrong-but-truthy payload would otherwise
        # satisfy this test, and the emitter would publish it as passing
        # evidence that export works.
        # `/skills/{id}/package` serves the PUBLISHED package, so this depends
        # on the re-publish above; without it the call is a bare NotFoundError
        # with nothing pointing at publication as the cause.
        package_download = service.download_skill_package(created.id)
        assert package_download.filename == f"{skill_name}.zip"
        assert package_download.content_type == "application/zip"
        assert package_download.content == package_bytes

        exported = service.export_skill_package(created.id)
        assert exported.filename == f"{skill_name}.zip"
        assert exported.content_type == "application/zip"
        assert exported.content == package_bytes

        bundle = service.export_skills_bundle([created.id])
        assert bundle.filename == "skills-export.zip"
        assert bundle.content_type == "application/zip"
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            assert archive.namelist() == [f"{skill_name}.zip"]
            assert archive.read(f"{skill_name}.zip") == package_bytes

        # --- deletion is soft, which only the stores can show -------------
        assert service.delete_skill(created.id) is True
        deleted_id = str(created.id)
        created_id_for_reimport_check = created.id
        created = None

        with pytest.raises(NotFoundError):
            service.get_skill(deleted_id)
        # The guarantee is "absent to every later read", not just to get_skill.
        # `deleted_id` is a str and SkillLibraryListItem.id is a UUID, so the
        # ids must be compared in one type - a str is never `in` a set of
        # UUIDs, and this assertion silently could not fail.
        assert deleted_id not in {
            str(item.id)
            for item in service.list_skills(q=skill_name, page_size=100).items
        }
        # NotFoundError, not the base APIError: the guarantee is that the skill
        # is ABSENT, and a 403 or a 500 from these routes is not absence. The
        # SDK translates only 404 to NotFoundError, so this is the difference
        # between "the read failed" and "the read said it is gone" - and the
        # get_skill check three lines up was already this precise.
        with pytest.raises(NotFoundError):
            service.export_skill_package(deleted_id)
        with pytest.raises(NotFoundError):
            service.download_skill_package(deleted_id)
        # "Deleting an already-deleted skill succeeds rather than 404ing" is a
        # stated guarantee with no other coverage.
        assert service.delete_skill(deleted_id) is True

        retained = fetch_skill_row(store, deleted_id)
        assert retained is not None, (
            "the row was destroyed; the capability claims deletion is soft"
        )
        assert retained.is_soft_deleted
        assert retained.content_checksum == package_digest

        # Soft delete and 409-on-duplicate share one mechanism: the partial
        # unique index covers only rows with `deleted_at IS NULL`. Nothing else
        # exercises that interlock, so a regression making soft-deleted names
        # permanently unusable would pass every other assertion here.
        reimported = service.import_skill_package(
            filename=f"{skill_name}.zip", file_content=package_bytes
        )
        try:
            assert reimported.name == skill_name
            assert reimported.id != created_id_for_reimport_check
        finally:
            try:
                service.delete_skill(reimported.id)
            except APIError as exc:
                logging.getLogger(__name__).warning(
                    "cleanup failed for re-imported skill %s: %s", reimported.id, exc
                )

        assert retained.storage_path == row.storage_path, (
            "the artifact was relocated; the guarantee is that it is left in place"
        )
        surviving_keys = list_package_objects(store, retained.storage_path)
        assert any(key.endswith("/package.zip") for key in surviving_keys), (
            "the package artifact was destroyed on delete"
        )
        # Retained has to mean the bytes survived. A delete that left the key
        # but truncated or replaced its body would satisfy an existence check
        # while the history the capability promises is gone.
        surviving_package = read_package_bytes(store, retained.storage_path)
        assert hashlib.sha256(surviving_package).hexdigest() == package_digest

    finally:
        if created is not None:
            try:
                service.delete_skill(created.id)
            except APIError as exc:
                # Cleanup failure must not mask the test's own verdict, but the
                # workspace rule is that nothing is swallowed silently: a leaked
                # skill explains a later duplicate-import conflict.
                logging.getLogger(__name__).warning(
                    "cleanup failed for skill %s: %s", created.id, exc
                )


def test_import_rejects_an_invalid_package(live_kamiwaza_client) -> None:
    """A package with no SKILL.md is refused.

    Says nothing about storage: this test has no store access, so "not stored"
    would be a claim it cannot make.

    Excluded from the capability mapping: on its own this proves only that a
    bad upload is rejected, which is no evidence that the library works.
    """

    service = live_kamiwaza_client.skills
    if not _skills_service_available(service):
        pytest.skip("Skills Library endpoints are unavailable in this environment")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("not-a-skill/readme.txt", "no SKILL.md here\n")

    accepted = None
    try:
        with pytest.raises(APIError) as excinfo:
            accepted = service.import_skill_package(
                filename="invalid-skill.zip",
                file_content=buffer.getvalue(),
            )
    except pytest.fail.Exception:
        # pytest.raises failed, so the platform ACCEPTED the bad package - the
        # very defect this test guards. Delete exactly what was created: unlike
        # the other tests here, this one's package and filename carry no uuid,
        # so an orphan makes the NEXT run of this same test answer 409 and fail
        # on the wrong assertion. Searching by name instead would both miss a
        # skill the platform named from the filename and soft-delete unrelated
        # skills that happened to match.
        if accepted is not None:
            service.delete_skill(accepted.id)
        raise

    if excinfo.value.status_code == 403:
        pytest.skip("Skills mutation paths require operator/admin access")
    assert excinfo.value.status_code == 400


def test_duplicate_import_conflicts(live_kamiwaza_client) -> None:
    """A second import of a name that is not soft-deleted conflicts.

    "Live" here means the partial unique index's sense - `(tenant_id, name)
    WHERE deleted_at IS NULL` - not "published". A draft conflicts too.

    Excluded from the capability mapping for the same reason as the invalid
    package case: a rejection assertion is not evidence the library works.
    """

    service = live_kamiwaza_client.skills
    if not _skills_service_available(service):
        pytest.skip("Skills Library endpoints are unavailable in this environment")

    unique_suffix = uuid4().hex[:8]
    skill_name = f"sdk-dup-{unique_suffix}"
    package_bytes = _build_skill_package_bytes(
        name=skill_name,
        display_name=f"SDK Duplicate {unique_suffix}",
        description="SDK duplicate-import probe",
    )

    created = None
    try:
        try:
            created = service.import_skill_package(
                filename=f"{skill_name}.zip", file_content=package_bytes
            )
        except APIError as exc:
            _require_mutation_available(exc)

        duplicate = None
        try:
            with pytest.raises(APIError) as excinfo:
                duplicate = service.import_skill_package(
                    filename=f"{skill_name}.zip", file_content=package_bytes
                )
        except pytest.fail.Exception:
            # Conflict handling regressed and the second import succeeded. The
            # `finally` below only knows about the first one, so without this
            # the duplicate stays active on a shared cluster.
            if duplicate is not None:
                service.delete_skill(duplicate.id)
            raise
        assert excinfo.value.status_code == 409

    finally:
        if created is not None:
            try:
                service.delete_skill(created.id)
            except APIError as exc:
                # Cleanup failure must not mask the test's own verdict, but the
                # workspace rule is that nothing is swallowed silently: a leaked
                # skill explains a later duplicate-import conflict.
                logging.getLogger(__name__).warning(
                    "cleanup failed for skill %s: %s", created.id, exc
                )


# --- ENG-11524: the skill itself, separately from the library -------------
#
# Drew's ask names these as two things: "does it behave correctly when
# invoked, not only 'does the library store and retrieve it'". The test below
# is the second half, and it is deliberately NOT in the capability mapping:
# the document is explicit that "the library shares skills; it does not
# execute them", so evidence about a skill's behaviour must not be attributed
# to the library's claim. Its round-trip half IS a library claim, but the two
# are fused here, so the honest placement is outside the mapping.


_SKILL_SCRIPT = '''\
"""A deterministic skill script - the unit under test when the skill is invoked."""

import argparse
import json
import sys

MARKER = "{marker}"


def main() -> int:
    parser = argparse.ArgumentParser(description="ENG-11524 invocation probe")
    parser.add_argument("--output", required=True)
    parser.add_argument("--values", nargs="+", type=int, required=True)
    args = parser.parse_args()

    payload = {{
        "marker": MARKER,
        "total": sum(args.values),
        "count": len(args.values),
    }}
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
    print(MARKER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def _build_executable_skill_package(*, name: str, marker: str) -> bytes:
    """A skill package whose ``scripts/`` carries a real, runnable script.

    Deterministic on purpose: the script prints ``marker`` and writes a JSON
    payload whose arithmetic is checkable, so "did the skill behave correctly
    when invoked" is a byte assertion rather than a judgement call.
    """

    skill_markdown = f"""---
name: {name}
description: ENG-11524 executable skill probe
metadata:
  kamiwaza:
    category: analysis
    tags:
      - integration
      - executable
---

# {name}

Run `scripts/run_skill.py --values N [N ...] --output PATH`. It writes a JSON
payload with the sum of the values and prints `{marker}`.
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{name}/SKILL.md", skill_markdown)
        archive.writestr(
            f"{name}/scripts/run_skill.py", _SKILL_SCRIPT.format(marker=marker)
        )
    return buffer.getvalue()


def test_exported_skill_package_executes_correctly(live_kamiwaza_client) -> None:
    """A skill survives the library byte-for-byte, and still works when run.

    Deliberately unmapped - see the note above.

    The script is extracted from the bytes the library returned. The assertion
    above already pins those bytes equal to what was uploaded, so this run does
    not add round-trip coverage - that ordering is the point: the package is
    known-identical to a fixture this module built, which is what makes
    extracting it and running it under ``sys.executable`` safe.
    """

    service = live_kamiwaza_client.skills
    if not _skills_service_available(service):
        pytest.skip("Skills Library endpoints are unavailable in this environment")

    unique_suffix = uuid4().hex[:8]
    skill_name = f"sdk-exec-{unique_suffix}"
    marker = f"ENG11524-OK-{unique_suffix}"
    package_bytes = _build_executable_skill_package(name=skill_name, marker=marker)

    created = None
    try:
        try:
            created = service.import_skill_package(
                filename=f"{skill_name}.zip", file_content=package_bytes
            )
        except APIError as exc:
            _require_mutation_available(exc)

        exported = service.export_skill_package(created.id)

        # The library must not rewrite what was vetted.
        assert exported.content == package_bytes, (
            "the exported package differs from the imported bytes"
        )

        with tempfile.TemporaryDirectory() as workdir:
            root = Path(workdir)
            with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
                archive.extractall(root)

            script = root / skill_name / "scripts" / "run_skill.py"
            assert script.is_file(), f"exported package has no script at {script}"

            output_path = root / "result.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--values",
                    "2",
                    "3",
                    "4",
                    "--output",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )

            assert completed.returncode == 0, (
                f"skill script failed ({completed.returncode}): {completed.stderr}"
            )
            assert marker in completed.stdout
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            assert payload == {"marker": marker, "total": 9, "count": 3}

    finally:
        if created is not None:
            try:
                service.delete_skill(created.id)
            except APIError as exc:
                # Cleanup failure must not mask the test's own verdict, but the
                # workspace rule is that nothing is swallowed silently: a leaked
                # skill explains a later duplicate-import conflict.
                logging.getLogger(__name__).warning(
                    "cleanup failed for skill %s: %s", created.id, exc
                )
