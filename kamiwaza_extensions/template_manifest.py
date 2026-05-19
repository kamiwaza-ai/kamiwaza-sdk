"""Template manifests for ``kz-ext update`` (ENG-3890 / D210 M2).

Pure data — no behavior. Each template shape (``app``, ``tool``, ``service``)
has a manifest enumerating the files the *template* owns, and a migration
list describing path renames between versions.

The CLI's ``UpdateCommand`` consumes these manifests to reconcile a
scaffolded extension against the current template:

* **Template-owned files** (everything in ``MANIFESTS[shape].files``) are
  re-rendered from the bundled template and diffed against the on-disk
  copy. Per-file ``strategy`` controls what happens on a mismatch.

* **Author-owned files** (everything *not* in the manifest, or explicitly
  on ``AUTHOR_OWNED_DENYLIST``) are never touched by ``update``.

A test in ``tests/unit/extensions/test_template_manifest.py`` enforces the
invariant that every file under ``templates/{shape}/`` is classified
exactly once. Adding a new file to a template without a manifest entry
fails CI loudly — by design, the contract has to be explicit.

``strategy`` values:

* ``overwrite`` — always replace with the new template-rendered content;
  write a ``.orig`` backup if the on-disk copy diverges from the prior
  template-rendered content (i.e. the author modified it).
* ``preserve_if_modified`` — replace if and only if the on-disk copy is
  unchanged from the prior template; otherwise skip with a warning.
* ``merge`` — field-level merge for JSON files; falls back to
  ``preserve_if_modified`` semantics for everything else. v1 implements
  this for ``kamiwaza.json`` only: rendered keys (the template's field
  set) seed the result, the on-disk file's values win for collisions,
  and CLI-controlled fields (``template_version``,
  ``template_shape``) are reset on every successful update. See
  ``commands/update._reconcile_json_merge`` for the implementation and
  ``docs/extensions/cli-reference/update.md`` for the author-facing
  contract. Non-JSON ``merge`` files behave identically to
  ``preserve_if_modified`` until a strategy-specific path is added.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from kamiwaza_extensions import __version__

FileStrategy = Literal["overwrite", "preserve_if_modified", "merge"]
ShapeName = Literal["app", "tool", "service"]


@dataclass(frozen=True)
class TemplateOwnedFile:
    """A path the template owns at this CLI version.

    ``since_version`` is the CLI version where this path first became
    template-owned; ``until_version`` is the last version where it was
    (None means still current). Both bounds are informational at the
    file level — ``UpdateCommand`` uses them when applying historical
    migrations across versions.
    """

    relative_path: str
    strategy: FileStrategy
    since_version: str
    until_version: str | None = None


@dataclass(frozen=True)
class TemplateMigration:
    """A path move that happened in a specific CLI version.

    ``UpdateCommand`` applies migrations in version order before computing
    diffs, so a renamed file follows its history rather than being seen
    as "old file deleted, new file appeared."
    """

    old_path: str
    new_path: str
    since_version: str


@dataclass(frozen=True)
class TemplateManifest:
    shape: ShapeName
    template_version: str
    files: tuple[TemplateOwnedFile, ...]
    migrations: tuple[TemplateMigration, ...]


# ---------------------------------------------------------------------------
# Helpers — keep manifest definitions concise and consistent.
# ---------------------------------------------------------------------------

# Track the CLI's __version__ so manifests don't drift from it on each
# release (review iteration-1 I9 + Suggestion: hard-coded version was a
# release-time foot-gun).
_M2_VERSION = __version__


def _owned(path: str, strategy: FileStrategy = "preserve_if_modified") -> TemplateOwnedFile:
    return TemplateOwnedFile(relative_path=path, strategy=strategy, since_version=_M2_VERSION)


# ---------------------------------------------------------------------------
# App template — the heaviest of the three shapes (Next.js frontend +
# FastAPI backend + Compose + scaffolded auth flow).
# ---------------------------------------------------------------------------

_APP_FILES: tuple[TemplateOwnedFile, ...] = (
    # ``kamiwaza.json`` is template-owned at the schema level — author owns
    # the values (name, version, type), template owns the field set.
    # ``merge`` strategy fans out to the field-level JSON merge path in
    # ``_reconcile_json_merge`` (rendered seeds the result, existing wins
    # on collision, manifest-controlled fields are stamped each update).
    _owned("kamiwaza.json", strategy="merge"),
    _owned(".gitignore", strategy="overwrite"),
    _owned("docker-compose.yml"),
    _owned("README.md"),
    _owned("AGENTS.md"),
    _owned("CLAUDE.md"),
    # Backend — Dockerfile + requirements + main.py shell. Author owns
    # the inside-of-main.py business logic but the file itself stays
    # template-owned because the scaffolded structure (FastAPI app object,
    # required routes) is part of the contract.
    _owned("backend/Dockerfile", strategy="overwrite"),
    _owned("backend/requirements.txt"),
    _owned("backend/app/main.py"),
    # Frontend infrastructure — pure scaffold, overwriting is fine.
    _owned("frontend/Dockerfile", strategy="overwrite"),
    _owned("frontend/next.config.js", strategy="overwrite"),
    _owned("frontend/package.json"),
    _owned("frontend/postcss.config.js", strategy="overwrite"),
    _owned("frontend/start.mjs", strategy="overwrite"),
    _owned("frontend/tailwind.config.ts", strategy="overwrite"),
    _owned("frontend/tsconfig.json", strategy="overwrite"),
    _owned("frontend/public/kmza-icon.png", strategy="overwrite"),
    # Frontend auth scaffolding — the platform contract. These are the
    # files that received the M1 P5/P6 fixes (anonymous-identity unify,
    # AuthGuard passthrough). Template-owned with preserve_if_modified
    # so an author who edited them gets a prompt rather than a silent
    # overwrite.
    _owned("frontend/src/app/api/[...path]/route.ts"),
    _owned("frontend/src/app/auth/login-url/route.ts"),
    _owned("frontend/src/app/auth/logout/route.ts"),
    _owned("frontend/src/app/session/route.ts"),
    _owned("frontend/src/app/layout.tsx"),
    _owned("frontend/src/app/providers.tsx"),
    _owned("frontend/src/app/logged-out/page.tsx"),
    _owned("frontend/src/components/NavBar.tsx"),
    # Local-dev auth bridge middleware — pass-through in production so
    # safe to commit to scaffolded extensions; only synthesizes envelope
    # headers when KZ_EXT_DEV_LOCAL_AUTH=1 (set by `kz-ext dev local
    # --auth`). See ENG-4318.
    _owned("frontend/src/middleware.ts"),
    # Author-owned in spirit — the home page and global stylesheet are
    # where authors will spend most of their time. They're listed in
    # AUTHOR_OWNED_DENYLIST below.
)


# ---------------------------------------------------------------------------
# Tool template — single-binary Python service.
# ---------------------------------------------------------------------------

_TOOL_FILES: tuple[TemplateOwnedFile, ...] = (
    _owned("kamiwaza.json", strategy="merge"),
    _owned(".gitignore", strategy="overwrite"),
    _owned("docker-compose.yml"),
    _owned("README.md"),
    _owned("Dockerfile", strategy="overwrite"),
    _owned("requirements.txt"),
    # Tool's src/server.py is the author's primary edit target. It's the
    # tool equivalent of the app's frontend/src/app/page.tsx — listed as
    # template-owned for first-render but with preserve_if_modified so a
    # modified server.py is never silently overwritten.
    _owned("src/server.py"),
)


# ---------------------------------------------------------------------------
# Service template — minimal: just the deploy contract files, no source.
# ---------------------------------------------------------------------------

_SERVICE_FILES: tuple[TemplateOwnedFile, ...] = (
    _owned("kamiwaza.json", strategy="merge"),
    _owned(".gitignore", strategy="overwrite"),
    _owned("docker-compose.yml"),
    _owned("README.md"),
    _owned("Dockerfile", strategy="overwrite"),
)


# ---------------------------------------------------------------------------
# Author-owned denylist. These files exist in the template directory as
# placeholder examples that the author is *expected* to replace; ``update``
# never reconciles them. Any file under ``templates/{shape}/`` that is not
# in MANIFESTS must be in the corresponding denylist (enforced by
# test_every_template_file_is_classified).
# ---------------------------------------------------------------------------

AUTHOR_OWNED_DENYLIST: dict[ShapeName, tuple[str, ...]] = {
    "app": (
        # The home page renders the demo greeting; authors will rewrite it.
        "frontend/src/app/page.tsx",
        # Tailwind globals — author may add design tokens.
        "frontend/src/app/globals.css",
    ),
    "tool": (),
    "service": (),
}


MANIFESTS: dict[ShapeName, TemplateManifest] = {
    "app": TemplateManifest(
        shape="app",
        template_version=_M2_VERSION,
        files=_APP_FILES,
        migrations=(),
    ),
    "tool": TemplateManifest(
        shape="tool",
        template_version=_M2_VERSION,
        files=_TOOL_FILES,
        migrations=(),
    ),
    "service": TemplateManifest(
        shape="service",
        template_version=_M2_VERSION,
        files=_SERVICE_FILES,
        migrations=(),
    ),
}


def current_template_version() -> str:
    """Return the template version this CLI ships."""
    return _M2_VERSION


def get_manifest(shape: str) -> TemplateManifest:
    """Look up the manifest for a shape; raise KeyError on unknown."""
    if shape not in MANIFESTS:
        raise KeyError(f"Unknown template shape: {shape!r}")
    return MANIFESTS[shape]  # type: ignore[index]


__all__ = [
    "AUTHOR_OWNED_DENYLIST",
    "MANIFESTS",
    "TemplateManifest",
    "TemplateMigration",
    "TemplateOwnedFile",
    "current_template_version",
    "get_manifest",
]
