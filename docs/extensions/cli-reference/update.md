# `kz-ext update`

Reconcile a scaffolded extension against the template shipped with the
current `kz-ext`. Template-owned files are re-rendered and applied per
their per-file strategy; author-owned files are never touched.

## Usage

```sh
kz-ext update [--dry-run] [--force] [--non-interactive] [--bootstrap]
```

| Flag | Behavior |
| --- | --- |
| (no flag) | Interactive — prompt on each conflict. |
| `--dry-run` | Print planned changes; no writes. |
| `--force` | Apply all template-owned updates without prompting; still writes `.orig` backups on author-modified files. |
| `--non-interactive` | Fail (non-zero exit) if any conflict would require prompting. CI use. |
| `--bootstrap` | Allowed only when the scaffold has no recorded `template_version`; treats current state as baseline and records the current CLI's template version without overwriting any file. |

## Algorithm (§4.2.3 of the system design)

1. Load `kamiwaza.json`; require `template_version` (or fail with exit 2 + "Run with --bootstrap" hint).
2. Load the `TemplateManifest` for `(template_shape, current CLI version)`.
3. Apply `TemplateMigration` entries in version order — for each `(old_path, new_path)` where `old_path` exists and `new_path` does not, move it.
4. For each `TemplateOwnedFile`:
   - Render from the bundled template with the project's context vars.
   - Compare rendered contents against on-disk contents.
   - If identical, skip.
   - If file is `kamiwaza.json` (strategy `merge`), do a field-level JSON merge — author values win for collisions, but `template_version` and `template_shape` are reset to manifest values.
   - Otherwise apply the per-file strategy (`overwrite`, `preserve_if_modified`).
5. Update `kamiwaza.json.template_version` to the manifest's version.
6. Print summary: `{updated, conflicts, skipped, unchanged, migrations}`.

## Strategies

| Strategy | What it does |
| --- | --- |
| `overwrite` | Always replace; write `.orig` backup if the on-disk copy diverges from the prior template render. |
| `preserve_if_modified` | Replace only if the on-disk copy is unchanged. If modified, prompt (interactive), apply with backup (`--force`), or skip (`--non-interactive`). |
| `merge` | JSON field-level merge for `kamiwaza.json`. Reserved keyword for future smart-merge of other JSON formats. |

## Errors

| Error | Exit | Hint |
| --- | --- | --- |
| `TemplateVersionMissing` | 2 | "Run with --bootstrap to adopt current state." |
| `TemplateShapeMismatch` | 2 | "Report as bug; manifest and scaffold are inconsistent." |
| `UnsupportedMigrationPath` | 2 | "Re-create the project with `kz-ext create` and port your changes." |
| Conflict in `--non-interactive` | 2 | Resolve manually or rerun without `--non-interactive`. |

## What `update` does NOT do

- It never deploys, builds, pushes, or modifies cluster state. That's `kz-ext dev`.
- It never bumps your extension's `version` field — only `template_version`, which describes which scaffold version your project is reconciled against.
- It never deletes author-owned files. The author-owned deny-list in `template_manifest.py` is opt-out by design — anything not template-owned stays put.

## Recovery

If `update` produces unwanted changes:

```sh
git diff           # review
git restore .     # roll back if needed
```

`.orig` backups remain on disk after an `--force` or `apply` action — delete them once you've confirmed the new content is what you want.
