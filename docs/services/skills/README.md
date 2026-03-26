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
- `classification`
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
