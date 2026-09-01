"""Focused dependency and ignore-file update regressions."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import typer

from kamiwaza_extensions.commands import update as upd
from kamiwaza_extensions.commands.update import _merge_dockerignore
from kamiwaza_extensions.exit_codes import ExitCode
from kamiwaza_extensions.scaffolder import Scaffolder


def test_pristine_package_receives_complete_template_update(tmp_path, monkeypatch):
    """A clean package manifest receives non-runtime dependency bumps."""
    monkeypatch.chdir(tmp_path)
    with patch("subprocess.run"):
        scaffold = Scaffolder().create(type_="app", name="my")
    package_path = scaffold / "frontend" / "package.json"
    metadata = json.loads((scaffold / "kamiwaza.json").read_text())
    assert "frontend/package.json" in metadata["template_file_hashes"]
    monkeypatch.chdir(scaffold)

    real_render = upd._render

    def render_with_new_tailwind_floor(template_path, context):
        text = real_render(template_path, context)
        if str(template_path).endswith("frontend/package.json"):
            return text.replace('"tailwindcss": "^3.4.19"', '"tailwindcss": "^9.9.9"')
        return text

    monkeypatch.setattr(upd, "_render", render_with_new_tailwind_floor)

    summary = upd.run_update(non_interactive=True)

    result = next(
        item for item in summary.files if item.relative_path == "frontend/package.json"
    )
    assert result.action == "updated"
    assert "clean" in result.reason
    package = json.loads(package_path.read_text())
    assert package["dependencies"]["tailwindcss"] == "^9.9.9"


@pytest.mark.parametrize("invalid_package", ["{\n", "[]\n"])
def test_invalid_package_blocks_noninteractive_update(
    invalid_package, tmp_path, monkeypatch
):
    """An unmergeable package must not be hidden by a version stamp."""
    monkeypatch.chdir(tmp_path)
    with patch("subprocess.run"):
        scaffold = Scaffolder().create(type_="app", name="my")

    metadata_path = scaffold / "kamiwaza.json"
    original_metadata = json.loads(metadata_path.read_text())
    package_path = scaffold / "frontend" / "package.json"
    package_path.write_text(invalid_package)

    original_manifest = upd.MANIFESTS["app"]
    bumped_manifest = upd.TemplateManifest(
        shape=original_manifest.shape,
        template_version="9.9.9-invalid-package-test",
        files=original_manifest.files,
        migrations=original_manifest.migrations,
    )
    monkeypatch.setitem(upd.MANIFESTS, "app", bumped_manifest)
    monkeypatch.chdir(scaffold)

    with pytest.raises(typer.Exit) as exc_info:
        upd.run_update(non_interactive=True)

    assert exc_info.value.exit_code == int(ExitCode.VALIDATION)
    assert package_path.read_text() == invalid_package
    updated_metadata = json.loads(metadata_path.read_text())
    assert updated_metadata["template_version"] == original_metadata["template_version"]


def test_force_replaces_invalid_package_with_backup(tmp_path, monkeypatch):
    """--force repairs an invalid structured manifest without losing it."""
    monkeypatch.chdir(tmp_path)
    with patch("subprocess.run"):
        scaffold = Scaffolder().create(type_="app", name="my")

    package_path = scaffold / "frontend" / "package.json"
    invalid_package = "{\n"
    package_path.write_text(invalid_package)
    monkeypatch.chdir(scaffold)

    summary = upd.run_update(force=True)

    package = json.loads(package_path.read_text())
    assert package["dependencies"]["@kamiwaza-ai/extensions-lib"] == ">=0.5 <0.6"
    assert package_path.with_name("package.json.orig").read_text() == invalid_package
    result = next(
        item for item in summary.files if item.relative_path == "frontend/package.json"
    )
    assert result.action == "applied"
    assert result.reason == "force (.orig backup)"


def test_requirements_update_preserves_runtime_extra_and_marker(tmp_path, monkeypatch):
    """Author-selected runtime constraints must survive the version sweep."""
    monkeypatch.chdir(tmp_path)
    with patch("subprocess.run"):
        scaffold = Scaffolder().create(type_="app", name="my")

    requirements_path = scaffold / "backend" / "requirements.txt"
    author_requirements = [
        line
        for line in requirements_path.read_text().splitlines()
        if not line.startswith("uvicorn")
    ]
    author_requirements = [
        (
            'kamiwaza-extensions-lib[asgi]>=0.4,<0.5; python_version < "3.13"'
            if line.startswith("kamiwaza-extensions-lib")
            else line
        )
        for line in author_requirements
    ]
    requirements_path.write_text("\n".join(author_requirements) + "\n")
    monkeypatch.chdir(scaffold)

    summary = upd.run_update(non_interactive=True)

    requirements = requirements_path.read_text().splitlines()
    assert (
        'kamiwaza-extensions-lib[asgi]>=0.5,<0.6; python_version < "3.13"'
        in requirements
    )
    assert not any(line.startswith("uvicorn") for line in requirements)
    result = next(
        item
        for item in summary.files
        if item.relative_path == "backend/requirements.txt"
    )
    assert result.action == "updated"
    assert result.reason == "requirements-merge"


def test_requirements_merge_preserves_distinct_conditional_branches():
    existing = "\n".join(
        (
            'kamiwaza-extensions-lib[asgi]>=0.4,<0.5; python_version < "3.11"',
            'kamiwaza-extensions-lib>=0.4,<0.5; python_version >= "3.11"',
            'kamiwaza_extensions_lib>=0.4,<0.5; python_version>="3.11"',
            "httpx>=0.27",
            "",
        )
    )
    rendered = "kamiwaza-extensions-lib>=0.5,<0.6\n"

    merged = upd._merge_requirements(existing, rendered)

    assert merged == "\n".join(
        (
            'kamiwaza-extensions-lib[asgi]>=0.5,<0.6; python_version < "3.11"',
            'kamiwaza-extensions-lib>=0.5,<0.6; python_version >= "3.11"',
            "httpx>=0.27",
            "",
        )
    )


def test_requirements_merge_preserves_marker_before_inline_comment():
    existing = "\n".join(
        (
            "kamiwaza-extensions-lib[asgi]>=0.4,<0.5; "
            'python_version < "3.11"  # legacy branch',
            "httpx @ https://example.test/httpx.whl#sha256=deadbeef",
            "",
        )
    )
    rendered = "kamiwaza-extensions-lib>=0.5,<0.6\n"

    merged = upd._merge_requirements(existing, rendered)

    assert merged == "\n".join(
        (
            "kamiwaza-extensions-lib[asgi]>=0.5,<0.6; "
            'python_version < "3.11"  # legacy branch',
            "httpx @ https://example.test/httpx.whl#sha256=deadbeef",
            "",
        )
    )


def test_requirements_merge_does_not_treat_url_semicolon_as_marker():
    existing = "kamiwaza-extensions-lib @ https://example.test/runtime.whl;param\n"
    rendered = "kamiwaza-extensions-lib>=0.5,<0.6\n"

    merged = upd._merge_requirements(existing, rendered)

    assert merged == rendered


def test_frontend_merge_reconciles_runtime_pins_in_secondary_maps():
    rendered = {
        "dependencies": {
            "@kamiwaza-ai/extensions-lib": ">=0.5 <0.6",
            "next": "15.5.24",
        }
    }
    existing = {
        "dependencies": {"next": "14.2.0"},
        "devDependencies": {"next": "14.2.0", "eslint": "^9"},
        "optionalDependencies": {"@kamiwaza-ai/extensions-lib": "^0.4"},
        "peerDependencies": {"next": "^14"},
        "overrides": {
            "next@^14": {".": "14.2.0", "sharp": "0.33.5"},
            "plugin": {
                "next": "14.2.0",
                "@scope/next": "9.9.9",
            },
            "@scope/next": "9.9.9",
            "react": "18.3.1",
        },
        "resolutions": {
            "**/@kamiwaza-ai/extensions-lib": "0.4.9",
            "**/next": "14.2.0",
            "**/@scope/next": "9.9.9",
        },
        "pnpm": {
            "overrides": {
                "next@*": "14.2.0",
                "@scope/next": "9.9.9",
                "react": "18.3.1",
            }
        },
    }
    merged = {**rendered, **existing}

    package = upd._merge_frontend_package(rendered, existing, merged)

    assert package["dependencies"]["next"] == "15.5.24"
    assert package["devDependencies"] == {"next": "15.5.24", "eslint": "^9"}
    assert package["optionalDependencies"]["@kamiwaza-ai/extensions-lib"] == (
        ">=0.5 <0.6"
    )
    assert package["peerDependencies"]["next"] == "15.5.24"
    assert package["overrides"] == {
        "next@^14": {".": "15.5.24", "sharp": "0.33.5"},
        "plugin": {
            "next": "15.5.24",
            "@scope/next": "9.9.9",
        },
        "@scope/next": "9.9.9",
        "react": "18.3.1",
    }
    assert package["resolutions"] == {
        "**/@kamiwaza-ai/extensions-lib": ">=0.5 <0.6",
        "**/next": "15.5.24",
        "**/@scope/next": "9.9.9",
    }
    assert package["pnpm"]["overrides"] == {
        "next@*": "15.5.24",
        "@scope/next": "9.9.9",
        "react": "18.3.1",
    }


def test_dockerignore_merge_preserves_crlf():
    existing = "# author rules\r\ncredentials/**\r\n"
    rendered = "node_modules\n.next\n.env*\n.git\n"

    merged = _merge_dockerignore(existing, rendered)

    assert "\n" not in merged.replace("\r\n", "")
    assert "credentials/**\r\n" in merged
    assert "node_modules\r\n" in merged
    assert ".git\r\n" in merged


def test_dockerignore_merge_preserves_author_negation_precedence():
    existing = "\n".join(
        (
            "# build-time public config",
            "*.env",
            "!*.example.env",
            "secret.example.env",
            "",
        )
    )
    rendered = "node_modules\n.next\n.env*\n.git\n"

    merged = _merge_dockerignore(existing, rendered)

    assert merged.splitlines()[-3:] == [
        "*.env",
        "!*.example.env",
        "secret.example.env",
    ]
