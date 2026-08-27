"""Focused dependency and ignore-file update regressions."""

from __future__ import annotations

import json
from unittest.mock import patch

from kamiwaza_extensions.commands import update as upd
from kamiwaza_extensions.commands.update import _merge_dockerignore
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


def test_dockerignore_merge_preserves_crlf():
    existing = "# author rules\r\ncredentials/**\r\n"
    rendered = "node_modules\n.next\n.env*\n.git\n"

    merged = _merge_dockerignore(existing, rendered)

    assert "\n" not in merged.replace("\r\n", "")
    assert "credentials/**\r\n" in merged
    assert "node_modules\r\n" in merged
    assert ".git\r\n" in merged
