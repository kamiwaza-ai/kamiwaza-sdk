# Skills Service

The Skills Service (`client.skills`) wraps the Skills Library endpoints exposed by the Kamiwaza backend. It supports browsing shared skills, importing new draft skills from zip packages, publishing via metadata updates, downloading published packages, and exporting one or more packages for operator workflows.

## Quick Start

```python
from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.schemas.skills import SkillLibraryUpdateRequest

client = KamiwazaClient(base_url="https://kamiwaza.test/api")
# ... authenticate ...

# Browse the library
skills = client.skills.list_skills(status="published", page_size=20)
for item in skills.items:
    print(item.name, item.category, item.tags)

# Import a new draft skill package
with open("pdf-generator.zip", "rb") as handle:
    created = client.skills.import_skill_package(
        filename="pdf-generator.zip",
        file_content=handle,
    )

# Publish the imported skill
published = client.skills.update_skill_metadata(
    created.id,
    SkillLibraryUpdateRequest(status="published"),
)

# Download the published package bytes
download = client.skills.download_skill_package(published.id)
print(download.filename, len(download.content))
```

## Methods

### `list_skills(...) -> SkillLibraryListResponse`

List skills visible to the current caller.

```python
skills = client.skills.list_skills(
    q="pdf",
    category="export",
    tag="reporting",
    status="published",
    page=1,
    page_size=50,
)
```

Notes:
- Non-operator callers only see published skills.
- `tag` filters against the top-level tags returned by the backend.

### `get_skill(skill_id) -> SkillLibraryDetailResponse`

Fetch detail for a single skill.

```python
detail = client.skills.get_skill("5b7f0d58-0dbf-4786-9d7f-8a815bb1a8a8")
print(detail.display_name)
print(detail.package_summary.entries)
```

### `import_skill_package(...) -> SkillLibraryDetailResponse`

Create a new draft skill by uploading an Agent Skills-compatible zip package.

```python
created = client.skills.import_skill_package(
    filename="chart-generator.zip",
    file_content=package_bytes,
)
assert created.status == "draft"
```

Important:
- Import is the creation path currently supported by the backend.
- This creates a draft skill; it does not publish automatically.

### `update_skill_metadata(skill_id, update) -> SkillLibraryDetailResponse`

Update mutable metadata for a skill.

```python
updated = client.skills.update_skill_metadata(
    created.id,
    SkillLibraryUpdateRequest(
        status="published",
        metadata={"tags": ["charts", "export"]},
    ),
)
```

Mutable fields currently supported:
- `display_name`
- `category`
- `marking` (optional normalized profile/level/attributes envelope)
- `status`
- `trigger`
- `inputs`
- `metadata`

### `download_skill_package(skill_id) -> SkillPackageDownload`

Download the published package for a skill.

```python
download = client.skills.download_skill_package(skill_id)
with open(download.filename, "wb") as handle:
    handle.write(download.content)
```

Important:
- This is a read path and follows published-skill visibility rules.
- The SDK preserves `filename`, `content_type`, and raw `content`.

### `export_skill_package(skill_id) -> SkillPackageDownload`

Export the current skill package for operator/admin workflows.

```python
exported = client.skills.export_skill_package(skill_id)
print(exported.filename)
```

Important:
- Export can be used for draft, published, or archived skills when allowed by backend authorization.

### `export_skills_bundle(skill_ids) -> SkillPackageDownload`

Export one or more skill packages as a single zip bundle.

```python
bundle = client.skills.export_skills_bundle([first_skill_id, second_skill_id])
print(bundle.filename)  # skills-export.zip
```

### `delete_skill(skill_id) -> bool`

Soft-delete a skill.

```python
client.skills.delete_skill(skill_id)
```

Returns `True` on success and raises `NotFoundError` if the skill does not exist.

## Schemas

Common models live in `kamiwaza_sdk.schemas.skills`:

- `SkillLibraryListItem`
- `SkillLibraryListResponse`
- `SkillLibraryDetailResponse`
- `SkillLibraryUpdateRequest`
- `SkillLibraryExportRequest`
- `SkillPackageDownload`

All response models allow extra fields so the SDK stays forward-compatible with backend additions.

## Current markings and portable snapshots (core 1.3)

`export_skill_package` and `export_skills_bundle` return version-2 snapshot ZIPs:
`manifest.json` contains current metadata and full marking envelopes; `skills/`
contains the unchanged original package ZIPs with SHA256 checksums. The stored
source checksum continues to identify the original package. A source download
through `download_skill_package` does not include subsequent metadata changes.

Pass a single-entry snapshot directly to `import_skill_package`; its current
marking is applied before the first persistence and the result is a draft.
Multi-entry snapshots require per-entry imports, not an atomic transaction.
Version-1 wrappers are unsupported.

For a raw package, supply `marking=Marking(...)` (or a full envelope dictionary)
to apply final protection in the initial multipart request. Omit `marking` to use
the package/snapshot value. Explicit `marking=None` sends JSON null, which the
server rejects if it would erase existing package protection. Invalid source
content or provider metadata is rejected before writes; an override cannot
bypass source validation or conflict with a snapshot's marking. Do not implement
initial protection as a separate metadata update after upload.
