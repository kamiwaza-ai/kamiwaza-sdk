"""TemplateManifest data module tests.

Issue: ENG-3890 / D210 M2 / Task T2.1.
Scenarios: TS-M2-12 (invariant: every template file is classified).

The manifest is pure data — no behavior. The hard test is the *invariant*
that every file under ``kamiwaza_extensions/templates/{shape}/`` is either
in the shape's manifest as template-owned, or in ``AUTHOR_OWNED_DENYLIST``
(scaffold-only files that the author is expected to replace and that
``kz-ext update`` should never reconcile). A new template file added without
a manifest entry should fail this test loudly, not slip through and break
the contract for existing scaffolds.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import pytest

from kamiwaza_extensions.template_manifest import (
    AUTHOR_OWNED_DENYLIST,
    MANIFESTS,
    TemplateManifest,
    TemplateMigration,
    TemplateOwnedFile,
)


@pytest.fixture(scope="module")
def template_root() -> Path:
    return Path(str(resources.files("kamiwaza_extensions") / "templates"))


# ---------------------------------------------------------------------------
# Data-module shape: the registry covers all 3 shapes with well-typed
# entries and no version-ordering surprises.
# ---------------------------------------------------------------------------


class TestRegistryShape:
    def test_manifests_cover_all_three_shapes(self):
        assert set(MANIFESTS.keys()) == {"app", "tool", "service"}

    @pytest.mark.parametrize("shape", ["app", "tool", "service"])
    def test_manifest_is_typed(self, shape: str):
        m = MANIFESTS[shape]
        assert isinstance(m, TemplateManifest)
        assert m.shape == shape
        assert all(isinstance(f, TemplateOwnedFile) for f in m.files)
        assert all(isinstance(mig, TemplateMigration) for mig in m.migrations)

    @pytest.mark.parametrize("shape", ["app", "tool", "service"])
    def test_files_have_unique_paths(self, shape: str):
        m = MANIFESTS[shape]
        paths = [f.relative_path for f in m.files]
        assert len(paths) == len(set(paths)), (
            f"Duplicate relative_path entries in {shape} manifest: "
            f"{[p for p in paths if paths.count(p) > 1]}"
        )

    @pytest.mark.parametrize("shape", ["app", "tool", "service"])
    def test_strategies_are_valid(self, shape: str):
        valid = {"overwrite", "preserve_if_modified", "merge"}
        for f in MANIFESTS[shape].files:
            assert (
                f.strategy in valid
            ), f"{shape}/{f.relative_path}: invalid strategy {f.strategy!r}"


# ---------------------------------------------------------------------------
# TS-M2-12 — the invariant. Every template file is either in the manifest
# or explicitly on the author-owned denylist. A new template file added
# without a manifest entry trips this.
# ---------------------------------------------------------------------------


@pytest.mark.extension_regression
@pytest.mark.parametrize("shape", ["app", "tool", "service"])
def test_every_template_file_is_classified(shape: str, template_root: Path):
    shape_dir = template_root / shape
    on_disk = sorted(
        str(p.relative_to(shape_dir)) for p in shape_dir.rglob("*") if p.is_file()
    )
    classified = {f.relative_path for f in MANIFESTS[shape].files} | set(
        AUTHOR_OWNED_DENYLIST.get(shape, ())
    )

    unclassified = [p for p in on_disk if p not in classified]
    assert not unclassified, (
        f"Files in templates/{shape}/ are neither in the manifest nor on "
        f"AUTHOR_OWNED_DENYLIST: {unclassified}\n"
        "Add new files to kamiwaza_extensions.template_manifest as either "
        "TemplateOwnedFile entries (reconciled by `kz-ext update`) or to "
        "AUTHOR_OWNED_DENYLIST (scaffold-only, never reconciled)."
    )


# ---------------------------------------------------------------------------
# Migration entries are well-formed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ["app", "tool", "service"])
def test_migrations_have_distinct_old_paths(shape: str):
    olds = [m.old_path for m in MANIFESTS[shape].migrations]
    assert len(olds) == len(set(olds)), (
        f"Duplicate migration old_path entries in {shape}: "
        f"{[p for p in olds if olds.count(p) > 1]}"
    )


# ---------------------------------------------------------------------------
# Tool template's FastMCP server uses the right API shape (ENG-3901 / F-014).
# ---------------------------------------------------------------------------


def test_tool_template_fastmcp_run_does_not_pass_host_or_port_kwargs(template_root):
    """ENG-3901 / F-014: ``FastMCP.run()`` accepts only ``transport`` and
    ``mount_path`` — NOT ``host`` or ``port``. Earlier tool template
    invoked ``mcp.run(transport="sse", host="0.0.0.0", port=8000)`` which
    is a TypeError on the current ``mcp>=1.0`` / ``fastmcp>=0.1`` API.
    Pod CrashLoopBackOff'd on every fresh tool deploy until host/port
    moved into the ``FastMCP(...)`` constructor."""
    import re

    server_py = (template_root / "tool" / "src" / "server.py").read_text()
    run_args = server_py.split("mcp.run(")[1].split(")")[0]
    # Must not pass host/port to .run() — word-boundary match so
    # ``transport="sse"`` doesn't false-positive against the substring
    # "port=".
    assert not re.search(r"\bhost\s*=", run_args), (
        f"tool template still passes host=... to FastMCP.run() — that's a "
        f"TypeError on the current API. host/port belong on the FastMCP() "
        f"constructor. run args: {run_args!r}"
    )
    assert not re.search(
        r"\bport\s*=", run_args
    ), f"tool template still passes port=... to FastMCP.run(). run args: {run_args!r}"
    # Must bind on 0.0.0.0 somewhere (otherwise loopback-only — unreachable
    # from outside the container).
    assert "0.0.0.0" in server_py, (
        "tool template must bind the FastMCP server on 0.0.0.0 (default "
        "127.0.0.1 only listens on loopback, unreachable from outside the "
        "container)"
    )
