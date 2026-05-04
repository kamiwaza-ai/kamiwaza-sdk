"""Shared monorepo conventions for kz-ext extension discovery.

Both ``kz-ext convert`` (via ``app_analyzer``) and the lifecycle
commands (via ``extension_detector``) need to know where extensions
typically live in monorepos. Keeping the conventions in one place
prevents drift — e.g., adding ``platforms/*`` here would surface in
both flows on the next call without touching either consumer.

The conventions are split into two groups:

- ``MONOREPO_PARENT_DIRS``: well-known parent directories that
  themselves contain one extension per child directory
  (``apps/<name>/``, ``tools/<name>/``, etc.). Used by both
  the analyzer (looking for ``<parent>/<name>/docker-compose.yml``)
  and the detector (looking for ``<parent>/<name>/kamiwaza.json``).

- ``MONOREPO_BARE_DIRS``: bare directory names that are themselves an
  extension when present at the workspace root (``app/``,
  ``extension/``). Convert uses these for greenfield containerization
  paths; the detector does not — a bare ``app/kamiwaza.json`` would be
  caught by its existing one-level shallow glob.
"""

from __future__ import annotations

MONOREPO_PARENT_DIRS = ("apps", "tools", "services", "packages", "extensions")
MONOREPO_BARE_DIRS = ("app", "extension")

# Directories analyzer + agent should never descend into when walking a
# repo. Includes git plumbing, language/framework build artifacts, and
# AI-tooling config (which often ships sample Dockerfiles / package
# manifests that aren't real services).
SKIP_DIRS = frozenset(
    {
        # Git and version control
        ".git",
        # Language / framework build artifacts and dependency caches
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".next",
        "build",
        "dist",
        "target",
        "coverage",
        # AI-tooling and IDE config dirs — never application source
        ".agents",
        ".claude",
        ".cursor",
        ".aider",
        ".specstory",
        ".idea",
        ".vscode",
        ".devcontainer",
    }
)
