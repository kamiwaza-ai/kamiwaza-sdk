"""Unit tests for the convert agent."""

import json

import pytest

pytestmark = pytest.mark.unit


class TestBuildPrompt:
    def test_includes_app_name(self):
        from kamiwaza_extensions.app_analyzer import AnalysisResult, ServiceInfo
        from kamiwaza_extensions.convert_agent import build_prompt
        from pathlib import Path

        result = AnalysisResult(
            app_dir=Path("/tmp/myapp"),
            app_name="myapp",
            services=[ServiceInfo(name="backend", language="python", ports=[8000])],
            file_contents={"main.py": "print('hello')"},
        )
        prompt = build_prompt(result)
        assert "myapp" in prompt
        assert "backend" in prompt
        assert "python" in prompt
        assert "kamiwaza-extensions-lib" in prompt

    def test_includes_compatibility_issues(self):
        from kamiwaza_extensions.app_analyzer import AnalysisResult
        from kamiwaza_extensions.convert_agent import build_prompt
        from pathlib import Path

        result = AnalysisResult(
            app_dir=Path("/tmp/myapp"),
            app_name="myapp",
            has_host_ports=["backend: 8000:8000"],
            missing_resource_limits=["backend"],
        )
        prompt = build_prompt(result)
        assert "8000:8000" in prompt
        assert "Missing resource limits" in prompt


class TestParseResponse:
    def test_parse_valid_json(self):
        from kamiwaza_extensions.convert_agent import parse_response

        response = json.dumps({
            "modifications": [
                {
                    "path": "requirements.txt",
                    "action": "modify",
                    "content": "fastapi\nkamiwaza-extensions-lib>=0.1.0\n",
                    "description": "Added runtime lib",
                }
            ],
            "manual_items": ["Check auth placement"],
            "summary": "Added SDK integration",
        })
        plan = parse_response(response)
        assert len(plan.modifications) == 1
        assert plan.modifications[0].path == "requirements.txt"
        assert plan.modifications[0].action == "modify"
        assert len(plan.manual_items) == 1
        assert plan.summary == "Added SDK integration"

    def test_parse_json_in_code_block(self):
        from kamiwaza_extensions.convert_agent import parse_response

        response = '```json\n{"modifications": [], "manual_items": [], "summary": "ok"}\n```'
        plan = parse_response(response)
        assert plan.summary == "ok"

    def test_parse_invalid_json(self):
        from kamiwaza_extensions.convert_agent import parse_response

        plan = parse_response("this is not json")
        assert len(plan.manual_items) > 0
        assert "could not be parsed" in plan.summary.lower() or "could not be parsed" in plan.manual_items[0].lower()


class TestApplyPlan:
    def test_create_file(self, tmp_path):
        from kamiwaza_extensions.convert_agent import apply_plan, ConversionPlan, FileModification

        plan = ConversionPlan(
            modifications=[
                FileModification(
                    path="kamiwaza.json",
                    action="create",
                    content='{"name": "test"}',
                    description="generated metadata",
                )
            ]
        )
        applied = apply_plan(plan, tmp_path)
        assert len(applied) == 1
        assert (tmp_path / "kamiwaza.json").exists()
        assert json.loads((tmp_path / "kamiwaza.json").read_text()) == {"name": "test"}

    def test_modify_file(self, tmp_path):
        from kamiwaza_extensions.convert_agent import apply_plan, ConversionPlan, FileModification

        (tmp_path / "requirements.txt").write_text("fastapi\n")
        plan = ConversionPlan(
            modifications=[
                FileModification(
                    path="requirements.txt",
                    action="modify",
                    content="fastapi\nkamiwaza-extensions-lib>=0.1.0\n",
                )
            ]
        )
        applied = apply_plan(plan, tmp_path)
        assert len(applied) == 1
        assert "kamiwaza-extensions-lib" in (tmp_path / "requirements.txt").read_text()

    def test_append_to_file(self, tmp_path):
        from kamiwaza_extensions.convert_agent import apply_plan, ConversionPlan, FileModification

        (tmp_path / "main.py").write_text("# existing\n")
        plan = ConversionPlan(
            modifications=[
                FileModification(
                    path="main.py",
                    action="append",
                    content="# new stuff\n",
                )
            ]
        )
        applied = apply_plan(plan, tmp_path)
        content = (tmp_path / "main.py").read_text()
        assert "existing" in content
        assert "new stuff" in content

    def test_dry_run_does_not_write(self, tmp_path):
        from kamiwaza_extensions.convert_agent import apply_plan, ConversionPlan, FileModification

        plan = ConversionPlan(
            modifications=[
                FileModification(path="new_file.py", action="create", content="# new")
            ]
        )
        applied = apply_plan(plan, tmp_path, dry_run=True)
        assert len(applied) == 1
        assert "[dry-run]" in applied[0]
        assert not (tmp_path / "new_file.py").exists()

    def test_creates_parent_dirs(self, tmp_path):
        from kamiwaza_extensions.convert_agent import apply_plan, ConversionPlan, FileModification

        plan = ConversionPlan(
            modifications=[
                FileModification(path="deep/nested/file.py", action="create", content="# hi")
            ]
        )
        apply_plan(plan, tmp_path)
        assert (tmp_path / "deep" / "nested" / "file.py").exists()

    def test_skips_empty_content(self, tmp_path):
        from kamiwaza_extensions.convert_agent import apply_plan, ConversionPlan, FileModification

        plan = ConversionPlan(
            modifications=[FileModification(path="", action="create", content="")]
        )
        applied = apply_plan(plan, tmp_path)
        assert len(applied) == 0

    def test_rejects_path_traversal(self, tmp_path):
        """Paths that escape app_dir via '..' must be rejected."""
        from kamiwaza_extensions.convert_agent import apply_plan, ConversionPlan, FileModification

        plan = ConversionPlan(
            modifications=[
                FileModification(
                    path="../../../etc/evil.conf",
                    action="create",
                    content="malicious",
                )
            ]
        )
        applied = apply_plan(plan, tmp_path)
        assert len(applied) == 0
        assert not (tmp_path.parent / "etc" / "evil.conf").exists()

    def test_rejects_absolute_path(self, tmp_path):
        """Absolute paths must be rejected."""
        from kamiwaza_extensions.convert_agent import apply_plan, ConversionPlan, FileModification

        plan = ConversionPlan(
            modifications=[
                FileModification(
                    path="/tmp/evil.py",
                    action="create",
                    content="malicious",
                )
            ]
        )
        applied = apply_plan(plan, tmp_path)
        assert len(applied) == 0


class TestRunAgent:
    def test_fallback_without_llm(self, monkeypatch):
        """When anthropic is not available, returns fallback plan."""
        from kamiwaza_extensions.app_analyzer import AnalysisResult
        from kamiwaza_extensions.convert_agent import run_agent
        from pathlib import Path

        # Ensure no API key
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        result = AnalysisResult(
            app_dir=Path("/tmp/test"),
            app_name="test",
        )
        plan = run_agent(result)
        assert "unavailable" in plan.summary.lower() or "basic" in plan.summary.lower()
        assert len(plan.manual_items) > 0
