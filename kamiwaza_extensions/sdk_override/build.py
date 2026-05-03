"""Build-time SDK overlay: bake the local SDK source into the image.

Used by ``kz-ext dev --sdk-repo`` (cluster deploy) and ``kz-ext dev
local --sdk-repo`` (local dev). Two layers per service:

1. **Pre-install strip** — drops the runtime-lib pin from
   ``requirements.txt`` / ``package.json`` before the install step so
   an unpublished version doesn't fail the build.
2. **Post-install overlay** — copies the local runtime-lib source into
   site-packages (Python) or installs it via ``npm pack`` (TypeScript)
   so the running container uses the local checkout.

The build-overlay path differs from ``compose.py`` in that it actually
modifies the Dockerfile content — the runtime-overlay path mounts and
exposes via env var, never touching the image.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from kamiwaza_extensions.sdk_override.classification import (
    _detect_build_service_runtime,
    _resolve_dockerfile,
)
from kamiwaza_extensions.sdk_override.spec import SdkOverrideSpec


@dataclass
class BuildOverride:
    """Override instructions for a single service's Docker build."""

    service_name: str
    overlay_steps: str  # Dockerfile lines appended/inserted into the original
    additional_build_contexts: Dict[str, str]
    insert_before_build: bool = False  # Insert before npm/next build line
    pre_install_steps: str = ""  # Inserted before RUN pip install -r requirements.txt
    # Which install pattern ``apply_build_overlay`` should match for the
    # ``pre_install_steps`` insert: "python" → ``RUN pip install -r
    # requirements.txt``, "typescript" → ``RUN npm install`` / ``RUN npm
    # ci``. None preserves the legacy "try both, Python first" behavior
    # for callers that don't set it (Codex P2 review on PR #91 round-4 —
    # without explicit language tagging, a frontend Dockerfile that also
    # runs ``pip install`` for build tooling would have the TS strip
    # inserted before the pip line, which is a no-op there because
    # ``package.json`` hasn't been copied yet).
    language: Optional[str] = None


_PYTHON_OVERLAY = (
    "# --- SDK override: install local Python runtime lib ---\n"
    "USER root\n"
    "COPY --from=sdk kamiwaza_extensions_lib /tmp/kamiwaza_extensions_lib\n"
    # Resolve the site-packages dir via ``sysconfig`` rather than by
    # importing ``kamiwaza_extensions_lib``. The pre-install strip
    # (above) removed the lib from requirements.txt, so it's NOT
    # installed via pip — importing it would crash here. ``purelib``
    # is the canonical pure-Python site-packages path for the current
    # interpreter and is always resolvable regardless of what's
    # installed. (ENG-3901 / F-002 round-3.)
    'RUN PURELIB=$(python -c "import sysconfig; print(sysconfig.get_paths()[\\"purelib\\"])")'
    ' && mkdir -p "$PURELIB"'
    ' && rm -rf "$PURELIB/kamiwaza_extensions_lib"'
    ' && cp -r /tmp/kamiwaza_extensions_lib "$PURELIB/"'
    " && rm -rf /tmp/kamiwaza_extensions_lib\n"
    "{restore_user_block}"
)

# Pattern that locates the standard scaffolded backend's pip install line so
# the pre-install strip step can be inserted before it.
_PYTHON_PIP_INSTALL_PATTERN = re.compile(
    # ``\b-r`` would not match because ``-`` is non-word and the preceding
    # space is also non-word, so the boundary doesn't apply. Use a literal
    # space instead. Trailing ``\b`` is fine — boundary between ``t`` and
    # newline / whitespace.
    r"^\s*RUN\s+.*\bpip\s+install\b.*\s-r\s+requirements\.txt\b",
    re.IGNORECASE,
)

# Pattern locating the frontend scaffold's npm install (``npm install`` or
# ``npm ci``). Matched per-line; ``RUN npm install`` and ``RUN npm ci`` are
# both accepted, optionally with flags before/after.
_TS_NPM_INSTALL_PATTERN = re.compile(
    r"^\s*RUN\s+.*\bnpm\s+(install|ci)\b", re.IGNORECASE
)

# Rewrites ``RUN ... npm ci ...`` to ``RUN ... npm install ...`` line-by-line.
# Required because the TS pre-install strip mutates ``package.json`` while
# leaving ``package-lock.json`` unchanged. ``npm ci`` enforces strict
# package.json ↔ lockfile parity and aborts on any divergence; ``npm install``
# consults the lockfile but tolerates mismatches and re-resolves. Local-build
# overrides already break strict lockfile reproducibility (we swap in a
# local source-built tarball at install time via ``_TS_OVERLAY``), so
# accepting looser install semantics is consistent and necessary
# (Codex P2 review on PR #91).
_TS_NPM_CI_LINE_PATTERN = re.compile(
    r"^(\s*RUN\s+.*\b)npm\s+ci\b", re.IGNORECASE | re.MULTILINE
)

# Drops the ``kamiwaza-extensions-lib`` pin from requirements.txt before pip
# install runs. The post-install ``_PYTHON_OVERLAY`` will copy the local
# source into site-packages, so removing the pin avoids a hard failure when
# the declared range isn't published yet (PR #89 dry-run finding F-002).
# Word-boundary check on the package name so prefix-aliases like
# ``kamiwaza-extensions-lib-extras`` are NOT stripped.
_PYTHON_PRE_INSTALL_STRIP = (
    "# --- SDK override: strip kamiwaza-extensions-lib from requirements.txt ---\n"
    "# The post-install overlay below copies the local runtime-lib source into\n"
    "# site-packages, so the PyPI install is redundant and would fail whenever\n"
    "# the pinned version is not yet published. See sdk_override.py docs.\n"
    "USER root\n"
    "RUN if [ -f requirements.txt ]; then"
    " sed -i -E '/^[[:space:]]*kamiwaza-extensions-lib($|[^A-Za-z0-9_-])/d'"
    " requirements.txt; fi\n"
    "{restore_user_block}"
)

# Drops ``@kamiwaza-ai/extensions-lib`` from every npm dependency-map
# field (the three documented dep maps + ``optionalDependencies``,
# ``bundleDependencies`` / ``bundledDependencies``, ``overrides``,
# ``resolutions``) in package.json before ``npm install`` runs. The
# post-install ``_TS_OVERLAY`` (or local-mode bind-mount + npm install)
# ships the runtime lib via ``npm pack``; removing the dep avoids a
# hard ETARGET failure when the declared version range isn't on the
# npm registry yet (mirror of ``_PYTHON_PRE_INSTALL_STRIP`` for the TS
# side).
#
# Implementation note: package.json is JSON, so a sed-line-strip would
# leave dangling commas. Use ``node -e`` (always present in the frontend
# image) to parse → mutate → write a structurally valid manifest.
_TS_PRE_INSTALL_STRIP = (
    "# --- SDK override: strip @kamiwaza-ai/extensions-lib from package.json ---\n"
    "# The post-install overlay below installs the local runtime-lib source\n"
    "# via ``npm pack``, so the registry install is redundant and would fail\n"
    "# whenever the pinned version is not yet published. Covers all five\n"
    "# dependency-map keys plus ``overrides`` / ``resolutions`` so a pin in\n"
    "# any of them won't survive the strip. Guarded with a file-exists check\n"
    "# so non-canonical Dockerfile layouts (no package.json at WORKDIR) fail\n"
    "# open instead of breaking the build.\n"
    "USER root\n"
    'RUN if [ -f package.json ]; then node -e "'
    "const fs=require('fs');"
    "const p=JSON.parse(fs.readFileSync('package.json','utf8'));"
    "const N='@kamiwaza-ai/extensions-lib';"
    "for(const k of ['dependencies','devDependencies','peerDependencies',"
    "'optionalDependencies','bundleDependencies','bundledDependencies',"
    "'overrides','resolutions'])"
    "{if(p[k]&&typeof p[k]==='object'&&!Array.isArray(p[k]))delete p[k][N];"
    " else if(Array.isArray(p[k]))p[k]=p[k].filter(x=>x!==N);}"
    "fs.writeFileSync('package.json', JSON.stringify(p,null,2)+'\\n');\";"
    " fi\n"
    "{restore_user_block}"
)

_TS_OVERLAY = (
    "# --- SDK override: install local TypeScript runtime lib ---\n"
    "USER root\n"
    "COPY --from=sdk kamiwaza-ai-extensions-lib /tmp/kamiwaza-ai-extensions-lib\n"
    "RUN TARBALL=$(cd /tmp/kamiwaza-ai-extensions-lib"
    " && npm pack --ignore-scripts --pack-destination /tmp 2>/dev/null | tail -1)"
    ' && cd /app && npm install --ignore-scripts "/tmp/$TARBALL"'
    " && rm -rf /tmp/kamiwaza-ai-extensions-lib*\n"
    "{restore_user_block}"
)

# Patterns that indicate the TS lib must be installed BEFORE this line
_TS_BUILD_PATTERNS = re.compile(
    r"^\s*RUN\s+.*(?:npm\s+run\s+build|next\s+build|yarn\s+build)", re.IGNORECASE
)


def generate_local_build_dockerfile_patches(
    spec: SdkOverrideSpec,
    compose_data: dict,
    extension_dir: Path,
) -> Dict[str, str]:
    """Per-service patched Dockerfile content for the ``dev local`` build phase.

    Returns ``{service_name: patched_dockerfile_source}`` for every backend
    (Python) and frontend (TypeScript/Node) service whose Dockerfile
    contains a recognizable install line. The caller is responsible for
    plumbing each value into a compose override (via
    ``build.dockerfile`` pointing at a temp file).

    Required because the runtime overlay (``generate_compose_override``)
    can only kick in once the image exists. When the scaffold's
    runtime-lib pin is not yet published on the language's package
    registry (PyPI / npm), the build's install step fails before any
    runtime overlay runs, leaving the developer with a hard build error
    and no path forward. The strip step inserted here makes each install
    succeed without the pin; the runtime overlay then surfaces the local
    source.

    Returns an empty dict when no service has a recognizable install line
    (e.g. a poetry-based custom Dockerfile, or a Node image that bakes
    deps differently — those users are responsible for their own
    runtime-lib install).
    """
    patches: Dict[str, str] = {}
    for svc_name, svc_config in compose_data.get("services", {}).items():
        patched = _build_patch_for_service(svc_config, spec, extension_dir)
        if patched is not None:
            patches[svc_name] = patched
    return patches


def _build_patch_for_service(
    svc_config: dict, spec: SdkOverrideSpec, extension_dir: Path
) -> Optional[str]:
    """Build the patched Dockerfile content for one service.

    Returns ``None`` when nothing applies (no build context, the
    service runtime doesn't match an enabled SDK lib, the Dockerfile
    is missing, or it has no recognizable install line). The
    multi-stage-aware classifier mirrors the cluster-deploy
    ``generate_build_overrides`` path so a ``FROM node AS builder;
    FROM nginx:alpine`` frontend still receives the TS strip on its
    builder stage (PR #91 round-3 H2).
    """
    if "build" not in svc_config:
        return None

    svc_runtime = _detect_build_service_runtime(
        "_", svc_config, extension_dir=extension_dir
    )
    if svc_runtime == "backend" and spec.python:
        pattern = _PYTHON_PIP_INSTALL_PATTERN
        strip_steps = _PYTHON_PRE_INSTALL_STRIP
    elif svc_runtime == "frontend" and spec.typescript:
        pattern = _TS_NPM_INSTALL_PATTERN
        strip_steps = _TS_PRE_INSTALL_STRIP
    else:
        return None

    df_path = _resolve_dockerfile(svc_config["build"], extension_dir)
    if df_path is None or not df_path.exists():
        return None

    original = df_path.read_text()
    patched = _insert_before_install_pattern(original, strip_steps, pattern)
    if patched == original:
        return None

    # Mirror the cluster-deploy ``apply_build_overlay`` behavior: if
    # the matched install line uses ``npm ci``, rewrite to ``npm
    # install`` so the package.json/lockfile divergence the strip
    # creates doesn't abort the build (PR #91 round-3 H1).
    if pattern is _TS_NPM_INSTALL_PATTERN:
        patched = _TS_NPM_CI_LINE_PATTERN.sub(r"\1npm install", patched)
    return patched


def generate_build_overrides(
    spec: SdkOverrideSpec,
    compose_data: dict,
    extension_dir: Optional[Path] = None,
) -> List[BuildOverride]:
    """Generate build overrides to bake local SDK source into images.

    For each service with a build context, produces Dockerfile overlay steps.
    For frontend services, if the Dockerfile contains a build step
    (``npm run build``, ``next build``), the overlay is inserted before that
    step so the local lib is compiled into the bundle.
    """
    overrides: List[BuildOverride] = []
    services = compose_data.get("services", {})

    for svc_name, svc_config in services.items():
        if "build" not in svc_config:
            continue

        svc_type = _detect_build_service_runtime(
            svc_name,
            svc_config,
            extension_dir=extension_dir,
        )

        if svc_type == "backend" and spec.python:
            overrides.append(
                BuildOverride(
                    service_name=svc_name,
                    overlay_steps=_PYTHON_OVERLAY,
                    additional_build_contexts={"sdk": str(spec.sdk_repo)},
                    pre_install_steps=_PYTHON_PRE_INSTALL_STRIP,
                    language="python",
                )
            )

        elif svc_type == "frontend" and spec.typescript:
            overrides.append(
                BuildOverride(
                    service_name=svc_name,
                    overlay_steps=_TS_OVERLAY,
                    additional_build_contexts={"sdk": str(spec.sdk_repo)},
                    insert_before_build=True,
                    # Mirror the backend's pre-install strip: drop
                    # ``@kamiwaza-ai/extensions-lib`` from package.json
                    # before ``npm install`` runs, otherwise the build
                    # ETARGET-fails when the pinned version isn't on the
                    # npm registry yet (ENG-3901 / F-002 round-2 — cluster
                    # deploy hit the same wall as dev local).
                    pre_install_steps=_TS_PRE_INSTALL_STRIP,
                    language="typescript",
                )
            )

    return overrides


def apply_build_overlay(dockerfile_content: str, overlay: BuildOverride) -> str:
    """Apply a build overlay to Dockerfile content.

    Two insertion points, applied in order:

    1. ``pre_install_steps`` — inserted immediately before the first
       ``RUN ... pip install -r requirements.txt`` (Python) or
       ``RUN npm install`` (TypeScript) line, so the runtime-lib pin
       can be stripped before the install runs. No-op when the
       Dockerfile has no matching install line.
    2. ``overlay_steps`` — inserted before a frontend build line when
       ``insert_before_build`` is True; appended at end otherwise.
    """
    content = _apply_pre_install_strip(dockerfile_content, overlay)
    return _splice_overlay_steps(content, overlay)


def _apply_pre_install_strip(content: str, overlay: BuildOverride) -> str:
    """Insert ``overlay.pre_install_steps`` before the first matching
    install line. Returns ``content`` unchanged when nothing to do."""
    if not overlay.pre_install_steps:
        return content

    # Pick the install pattern based on the overlay's declared
    # language. A frontend Dockerfile that also runs ``pip install``
    # for build tooling would otherwise have the TS strip inserted
    # before the pip line (no-op, because package.json hasn't been
    # copied yet) and the actual ``npm install`` would still hit the
    # unstripped pin (Codex P2 review on PR #91 round-4).
    if overlay.language == "python":
        patterns: Tuple["re.Pattern[str]", ...] = (_PYTHON_PIP_INSTALL_PATTERN,)
    elif overlay.language == "typescript":
        patterns = (_TS_NPM_INSTALL_PATTERN,)
    else:
        # Legacy fallback for callers that don't declare a language —
        # try Python first, fall through to TS.
        patterns = (_PYTHON_PIP_INSTALL_PATTERN, _TS_NPM_INSTALL_PATTERN)

    for pattern in patterns:
        new_content = _insert_before_install_pattern(
            content, overlay.pre_install_steps, pattern
        )
        if new_content is not content:
            # If the matched install line uses ``npm ci``, rewrite to
            # ``npm install`` so the lockfile mismatch the strip step
            # creates doesn't abort the build (Codex P2 review on PR #91).
            if pattern is _TS_NPM_INSTALL_PATTERN:
                new_content = _TS_NPM_CI_LINE_PATTERN.sub(
                    r"\1npm install", new_content
                )
            return new_content
    return content


def _splice_overlay_steps(content: str, overlay: BuildOverride) -> str:
    """Splice ``overlay.overlay_steps`` either before a build line
    (when ``insert_before_build`` is True) or appended at end."""
    lines = content.splitlines(keepends=True)
    insert_idx: Optional[int] = None

    if overlay.insert_before_build:
        for i, line in enumerate(lines):
            if _TS_BUILD_PATTERNS.match(line):
                insert_idx = i
                break

    user_scope = lines[:insert_idx] if insert_idx is not None else lines
    overlay_steps = overlay.overlay_steps.replace(
        "{restore_user_block}",
        _restore_user_block(_find_active_user(user_scope)),
    )

    if insert_idx is not None:
        return (
            "".join(lines[:insert_idx])
            + "\n"
            + overlay_steps
            + "\n"
            + "".join(lines[insert_idx:])
        )
    # No build line found or not insert_before_build — append at end
    return content.rstrip() + "\n\n" + overlay_steps


def _insert_before_install_pattern(
    dockerfile_content: str,
    pre_steps: str,
    pattern: "re.Pattern[str]",
) -> str:
    """Insert ``pre_steps`` immediately before the first line matching
    ``pattern`` (e.g. ``RUN pip install -r requirements.txt`` or
    ``RUN npm install``). Returns the content unchanged when no such line
    is present — the user's Dockerfile is then responsible for runtime-lib
    install on its own, and the post-install overlay (if any) still
    appends as before."""
    lines = dockerfile_content.splitlines(keepends=True)
    insert_idx = None
    for i, line in enumerate(lines):
        if pattern.match(line):
            insert_idx = i
            break
    if insert_idx is None:
        return dockerfile_content
    pre_steps_resolved = pre_steps.replace(
        "{restore_user_block}",
        _restore_user_block(_find_active_user(lines[:insert_idx])),
    )
    # Ensure the inserted block starts on its own line and doesn't fuse with
    # the preceding directive.
    leading = (
        "" if not lines[:insert_idx] or lines[insert_idx - 1].endswith("\n") else "\n"
    )
    trailing = "" if pre_steps_resolved.endswith("\n") else "\n"
    return (
        "".join(lines[:insert_idx])
        + leading
        + pre_steps_resolved
        + trailing
        + "".join(lines[insert_idx:])
    )


def _find_active_user(lines: List[str]) -> Optional[str]:
    """Return the last USER declared in the given Dockerfile lines."""
    active_user = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("USER "):
            user = stripped[len("USER ") :].strip()
            if user:
                active_user = user
    return active_user


def _restore_user_block(user: Optional[str]) -> str:
    """Render a USER restore directive when the Dockerfile was non-root."""
    if not user or user.lower() == "root":
        return ""
    return f"USER {user}\n"
