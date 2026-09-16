# Compatibility-aware catalog publishing

Extensions already declare platform support using `kamiwaza_version` in
`kamiwaza.json`. For example, `">=1.3.1"` requires Kamiwaza 1.3.1 or newer.
This is separate from `kz_ext_version` (the CLI requirement). Registry building
preserves this metadata and unknown fields.

The platform constraint grammar is numeric `major.minor[.patch]` with optional
`>=`, `<=`, `>`, `<`, `==`, `!=` operators and comma conjunctions. Bare versions
mean exact equality. Surrounding clause whitespace is accepted, but whitespace
between an operator and number is not. Absent, null, empty, and `*` constraints
mean unrestricted support. Malformed clauses fail closed. General PEP 440
operators such as `~=` and wildcard clauses like `==1.*` are not supported by
Core and therefore cannot be authored by this publisher.

## Explicit generation, unchanged legacy defaults

`kz-ext publish --catalog-schema compat-v1` opts into
`<prefix>/garden/compat-v1/apps.json` or `tools.json`. Services use apps.json.
Connectors are intentionally excluded because their contract is different.
Default publishing remains v3; `--catalog-schema 2` is also unchanged.
Do not redirect existing readers or legacy writers to compat-v1. Provision
separate credentials restricted to this prefix; an SDK capability check is not
a substitute for storage authorization. No production catalog migration is
automatic.

Publishing CI can require the machine-readable capability before selecting
this generation:

```sh
kz-ext catalog-capabilities | python -c 'import json,sys; assert "compat-v1-cas" in json.load(sys.stdin)["capabilities"]'
```

Distinct `(name, normalized extension version)` declarations coexist even if
platform ranges overlap. This preserves unconstrained fallbacks and permits
publishing an older maintenance release after newer releases. Versions use
`packaging.version.Version` identity and ordering, matching Core selection:
`1.0` equals `1.0.0`, development/prerelease versions precede final releases,
and post releases follow them. Use a separate staging catalog when publishing
previews; a prerelease of a later release can rank above an older final release.
Existing same-version declarations require explicit `--force`, which replaces
only that release, including its revision; it never deletes siblings.

## Conditional commit safety

The new generation uses S3 `PutObject` with the exact read ETag (`IfMatch`), or
`IfNoneMatch='*'` for first creation. Conflicts re-read/re-merge, at most five
attempts. boto3/botocore 1.35.70 or later is required. The capability command checks the
installed operation model without credentials or network access, and direct
publishing checks it before preview upload. The backend must enforce conditional
puts; unsupported operations fail closed. Never replace this with unconditional
uploads. Successful conditional write is the commit receipt; a later reader may
already observe another publisher's valid commit.

There are no expiring advisory locks or catalog rollback writes in this path.
Thus an expired publisher cannot delete a successor lock or restore stale data
over another publisher. Preview images are content-addressed and uploaded
before catalog commit; a failed transaction can leave an unreferenced image but
cannot change an earlier release's preview. Legacy generation behavior is
unchanged.

The local S3 qualification script under `tests/qualification/extension_compatibility/` uses the
real CatalogPublisher against an explicitly supplied localhost endpoint and
isolated bucket. It checks history and conditional-write enforcement; no
production credentials or endpoints are needed.
