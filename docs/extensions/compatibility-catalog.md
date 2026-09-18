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

`kz-ext publish --stage isolated --catalog-schema compat-v1` opts into
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
publishing an older maintenance release after newer releases. New releases require SemVer `major.minor.patch` with optional prerelease and
build metadata. Numeric components sort numerically; prereleases precede final
versions. Build metadata distinguishes immutable identity but does not change
precedence. Legacy two-component versions are recognized only when reading
existing records; new publications require all three components. PEP 440 dev,
post and local-version syntax is not accepted. Use a separate staging catalog
when publishing previews.
Published `(name, version)` identities are immutable. Repeating identical
content is a successful no-op; changing constraints, release notes, revision,
Compose, image digests, or any other release metadata requires a new version.
`--force` never grants overwrite permission in this generation. This also applies
to development and staging catalogs: use a new prerelease version for changed
content. Existing numeric generation behavior is unchanged.

Every Compose service image and every declared `docker_images` or
`extra_docker_images` reference must include `@sha256:<64 lowercase hex digits>`.
The service-image inventory must match Compose exactly. Runtime builds and
unresolved image substitutions are rejected at the direct publisher API, even
when the CLI is bypassed. External and prebuilt images must be pinned by their
author; they are not exempt from validation. For multi-platform images use the
OCI index digest, so architecture selection still works. The existing CLI digest
resolver reads that index digest, rather than selecting a local platform image.
Applications downloading undeclared artifacts are outside this manifest contract.
Keep referenced registry objects retained: digest identity does not ensure storage
availability.

Inline `release_notes` can be retained as immutable metadata. Declared
compatibility is not certification. Consumers retain baseline, availability,
and local selections separately; refreshing published availability must not
change a selection or an existing deployment.

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

Before a live compat-v1 CLI build or push, the publisher reads and validates the
catalog and rejects an already published normalized `(name, version)` identity,
even if its intended content is identical or `--force` is supplied. Catalog read
failures stop publication before either phase. To repeat an existing release
without modifying registry tags, use `--no-build --no-push`; final immutable
content validation still applies.

This preflight is a read, not an atomic reservation of registry tags. Two first
publishers can race before either catalog commit, and upstream CI or other
registry clients can still retag images. Configure registry tag immutability and
retention for published digest objects independently. Catalog compare-and-swap
and digest references prevent catalog content replacement; they cannot prevent
external garbage collection or guarantee ongoing artifact availability.

### Dry-run qualification

For buildable services whose output digests are not supplied, `--dry-run`
(including `--all`) reports **PLAN ONLY**: artifact identity and catalog
immutability remain unverified. It performs no build, registry push or catalog
write. With complete immutable artifact references, dry-run performs the exact
catalog comparison, including content-addressed preview images. Planning success
is not permission to overwrite an existing release; live publication still
requires all exact artifacts and immutable catalog checks.
